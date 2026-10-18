// tests/go/cookiejar.go：对拍 Go 1.27 的 net/http/cookiejar。
// cookiejar 的 setCookies/cookies 带 now 参数的版本没有导出，所以这里把 jar.go 和 punycode.go
// 原样复制进 package main（只去掉了 sync.Mutex，并把 net/http/internal/ascii 换成本地同名函数），
// 测试用固定时间 tNow，和 jar_test.go 里的手段一样。下面的测试表直接复制自 jar_test.go / punycode_test.go。
package main

import (
	"cmp"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"slices"
	"strings"
	"time"
	"unicode/utf8"
)

func asciiLower(b byte) byte {
	if 'A' <= b && b <= 'Z' {
		return b + ('a' - 'A')
	}
	return b
}

func asciiEqualFold(s, t string) bool {
	if len(s) != len(t) {
		return false
	}
	for i := 0; i < len(s); i++ {
		if asciiLower(s[i]) != asciiLower(t[i]) {
			return false
		}
	}
	return true
}

func asciiIsPrint(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] < ' ' || s[i] > '~' {
			return false
		}
	}
	return true
}

func asciiIs(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] > 0x7f {
			return false
		}
	}
	return true
}

func asciiToLower(s string) (string, bool) {
	if !asciiIsPrint(s) {
		return "", false
	}
	return strings.ToLower(s), true
}

// ───────────── 复制自 net/http/cookiejar/jar.go ─────────────

// PublicSuffixList provides the public suffix of a domain. For example:
//   - the public suffix of "example.com" is "com",
//   - the public suffix of "foo1.foo2.foo3.co.uk" is "co.uk", and
//   - the public suffix of "bar.pvt.k12.ma.us" is "pvt.k12.ma.us".
//
// Implementations of PublicSuffixList must be safe for concurrent use by
// multiple goroutines.
//
// An implementation that always returns "" is valid and may be useful for
// testing but it is not secure: it means that the HTTP server for foo.com can
// set a cookie for bar.com.
//
// A public suffix list implementation is in the package
// [golang.org/x/net/publicsuffix].
type PublicSuffixList interface {
	// PublicSuffix returns the public suffix of domain.
	//
	// TODO: specify which of the caller and callee is responsible for IP
	// addresses, for leading and trailing dots, for case sensitivity, and
	// for IDN/Punycode.
	PublicSuffix(domain string) string

	// String returns a description of the source of this public suffix
	// list. The description will typically contain something like a time
	// stamp or version number.
	String() string
}

// Options are the options for creating a new [Jar].
type Options struct {
	// PublicSuffixList is the public suffix list that determines whether
	// an HTTP server can set a cookie for a domain.
	//
	// A nil value is valid and may be useful for testing but it is not
	// secure: it means that the HTTP server for foo.co.uk can set a cookie
	// for bar.co.uk.
	PublicSuffixList PublicSuffixList
}

// Jar implements the [net/http.CookieJar] interface.
type Jar struct {
	psList PublicSuffixList

	// entries is a set of entries, keyed by their eTLD+1 and subkeyed by
	// their name/domain/path.
	entries map[string]map[string]entry

	// nextSeqNum is the next sequence number assigned to a new cookie
	// created SetCookies.
	nextSeqNum uint64
}

// New returns a new cookie jar. A nil [*Options] is equivalent to a zero
// Options.
func New(o *Options) (*Jar, error) {
	jar := &Jar{
		entries: make(map[string]map[string]entry),
	}
	if o != nil {
		jar.psList = o.PublicSuffixList
	}
	return jar, nil
}

// entry is the internal representation of a cookie.
//
// This struct type is not used outside of this package per se, but the exported
// fields are those of RFC 6265.
type entry struct {
	Name       string
	Value      string
	Quoted     bool
	Domain     string
	Path       string
	SameSite   string
	Secure     bool
	HttpOnly   bool
	Persistent bool
	HostOnly   bool
	Expires    time.Time
	Creation   time.Time
	LastAccess time.Time

	// seqNum is a sequence number so that Cookies returns cookies in a
	// deterministic order, even for cookies that have equal Path length and
	// equal Creation time. This simplifies testing.
	seqNum uint64
}

// id returns the domain;path;name triple of e as an id.
func (e *entry) id() string {
	return fmt.Sprintf("%s;%s;%s", e.Domain, e.Path, e.Name)
}

// shouldSend determines whether e's cookie qualifies to be included in a
// request to host/path. It is the caller's responsibility to check if the
// cookie is expired.
func (e *entry) shouldSend(https bool, host, path string) bool {
	return e.domainMatch(host) && e.pathMatch(path) && e.secureMatch(https)
}

// domainMatch checks whether e's Domain allows sending e back to host.
// It differs from "domain-match" of RFC 6265 section 5.1.3 because we treat
// a cookie with an IP address in the Domain always as a host cookie.
func (e *entry) domainMatch(host string) bool {
	if e.Domain == host {
		return true
	}
	return !e.HostOnly && hasDotSuffix(host, e.Domain)
}

// pathMatch implements "path-match" according to RFC 6265 section 5.1.4.
func (e *entry) pathMatch(requestPath string) bool {
	if requestPath == e.Path {
		return true
	}
	if strings.HasPrefix(requestPath, e.Path) {
		if e.Path[len(e.Path)-1] == '/' {
			return true // The "/any/" matches "/any/path" case.
		} else if requestPath[len(e.Path)] == '/' {
			return true // The "/any" matches "/any/path" case.
		}
	}
	return false
}

// secureMatch checks whether a cookie should be sent based on the protocol
// and the Secure flag. Localhost is considered a secure origin regardless
// of protocol, matching browser behavior.
func (e *entry) secureMatch(https bool) bool {
	if !e.Secure {
		// Cookies not marked secure are always sent.
		return true
	}
	// Everything below is about cookies marked secure.
	if https {
		// HTTPS request matches secure cookies.
		return true
	}
	// Consider localhost to be secure like browsers.
	if isLocalhost(e.Domain) {
		return true
	}
	ip, err := netip.ParseAddr(e.Domain)
	if err == nil && ip.IsLoopback() {
		return true
	}
	return false
}

func isLocalhost(host string) bool {
	host = strings.TrimSuffix(host, ".")
	if idx := strings.LastIndex(host, "."); idx >= 0 {
		host = host[idx+1:]
	}
	return asciiEqualFold(host, "localhost")
}

// hasDotSuffix reports whether s ends in "."+suffix.
func hasDotSuffix(s, suffix string) bool {
	return len(s) > len(suffix) && s[len(s)-len(suffix)-1] == '.' && s[len(s)-len(suffix):] == suffix
}

// Cookies implements the Cookies method of the [http.CookieJar] interface.
//
// It returns an empty slice if the URL's scheme is not HTTP or HTTPS.
func (j *Jar) Cookies(u *url.URL) (cookies []*http.Cookie) {
	return j.cookies(u, time.Now())
}

// cookies is like Cookies but takes the current time as a parameter.
func (j *Jar) cookies(u *url.URL, now time.Time) (cookies []*http.Cookie) {
	if u.Scheme != "http" && u.Scheme != "https" {
		return cookies
	}
	host, err := canonicalHost(u.Host)
	if err != nil {
		return cookies
	}
	key := jarKey(host, j.psList)

	submap := j.entries[key]
	if submap == nil {
		return cookies
	}

	https := u.Scheme == "https"
	path := u.Path
	if path == "" {
		path = "/"
	}

	modified := false
	var selected []entry
	for id, e := range submap {
		if e.Persistent && !e.Expires.After(now) {
			delete(submap, id)
			modified = true
			continue
		}
		if !e.shouldSend(https, host, path) {
			continue
		}
		e.LastAccess = now
		submap[id] = e
		selected = append(selected, e)
		modified = true
	}
	if modified {
		if len(submap) == 0 {
			delete(j.entries, key)
		} else {
			j.entries[key] = submap
		}
	}

	// sort according to RFC 6265 section 5.4 point 2: by longest
	// path and then by earliest creation time.
	slices.SortFunc(selected, func(a, b entry) int {
		if r := cmp.Compare(b.Path, a.Path); r != 0 {
			return r
		}
		if r := a.Creation.Compare(b.Creation); r != 0 {
			return r
		}
		return cmp.Compare(a.seqNum, b.seqNum)
	})
	for _, e := range selected {
		cookies = append(cookies, &http.Cookie{Name: e.Name, Value: e.Value, Quoted: e.Quoted})
	}

	return cookies
}

// SetCookies implements the SetCookies method of the [http.CookieJar] interface.
//
// It does nothing if the URL's scheme is not HTTP or HTTPS.
func (j *Jar) SetCookies(u *url.URL, cookies []*http.Cookie) {
	j.setCookies(u, cookies, time.Now())
}

// setCookies is like SetCookies but takes the current time as parameter.
func (j *Jar) setCookies(u *url.URL, cookies []*http.Cookie, now time.Time) {
	if len(cookies) == 0 {
		return
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return
	}
	host, err := canonicalHost(u.Host)
	if err != nil {
		return
	}
	key := jarKey(host, j.psList)
	defPath := defaultPath(u.Path)

	submap := j.entries[key]

	modified := false
	for _, cookie := range cookies {
		e, remove, err := j.newEntry(cookie, now, defPath, host)
		if err != nil {
			continue
		}
		id := e.id()
		if remove {
			if submap != nil {
				if _, ok := submap[id]; ok {
					delete(submap, id)
					modified = true
				}
			}
			continue
		}
		if submap == nil {
			submap = make(map[string]entry)
		}

		if old, ok := submap[id]; ok {
			e.Creation = old.Creation
			e.seqNum = old.seqNum
		} else {
			e.Creation = now
			e.seqNum = j.nextSeqNum
			j.nextSeqNum++
		}
		e.LastAccess = now
		submap[id] = e
		modified = true
	}

	if modified {
		if len(submap) == 0 {
			delete(j.entries, key)
		} else {
			j.entries[key] = submap
		}
	}
}

// canonicalHost strips port from host if present and returns the canonicalized
// host name.
func canonicalHost(host string) (string, error) {
	var err error
	if hasPort(host) {
		host, _, err = net.SplitHostPort(host)
		if err != nil {
			return "", err
		}
	}
	// Strip trailing dot from fully qualified domain names.
	host = strings.TrimSuffix(host, ".")
	encoded, err := toASCII(host)
	if err != nil {
		return "", err
	}
	// We know this is ascii, no need to check.
	lower, _ := asciiToLower(encoded)
	return lower, nil
}

// hasPort reports whether host contains a port number. host may be a host
// name, an IPv4 or an IPv6 address.
func hasPort(host string) bool {
	colons := strings.Count(host, ":")
	if colons == 0 {
		return false
	}
	if colons == 1 {
		return true
	}
	return host[0] == '[' && strings.Contains(host, "]:")
}

// jarKey returns the key to use for a jar.
func jarKey(host string, psl PublicSuffixList) string {
	if isIP(host) {
		return host
	}

	var i int
	if psl == nil {
		i = strings.LastIndex(host, ".")
		if i <= 0 {
			return host
		}
	} else {
		suffix := psl.PublicSuffix(host)
		if suffix == host {
			return host
		}
		i = len(host) - len(suffix)
		if i <= 0 || host[i-1] != '.' {
			// The provided public suffix list psl is broken.
			// Storing cookies under host is a safe stopgap.
			return host
		}
		// Only len(suffix) is used to determine the jar key from
		// here on, so it is okay if psl.PublicSuffix("www.buggy.psl")
		// returns "com" as the jar key is generated from host.
	}
	prevDot := strings.LastIndex(host[:i-1], ".")
	return host[prevDot+1:]
}

// isIP reports whether host is an IP address.
func isIP(host string) bool {
	if strings.ContainsAny(host, ":%") {
		// Probable IPv6 address.
		// Hostnames can't contain : or %, so this is definitely not a valid host.
		// Treating it as an IP is the more conservative option, and avoids the risk
		// of interpreting ::1%.www.example.com as a subdomain of www.example.com.
		return true
	}
	return net.ParseIP(host) != nil
}

// defaultPath returns the directory part of a URL's path according to
// RFC 6265 section 5.1.4.
func defaultPath(path string) string {
	if len(path) == 0 || path[0] != '/' {
		return "/" // Path is empty or malformed.
	}

	i := strings.LastIndex(path, "/") // Path starts with "/", so i != -1.
	if i == 0 {
		return "/" // Path has the form "/abc".
	}
	return path[:i] // Path is either of form "/abc/xyz" or "/abc/xyz/".
}

// newEntry creates an entry from an http.Cookie c. now is the current time and
// is compared to c.Expires to determine deletion of c. defPath and host are the
// default-path and the canonical host name of the URL c was received from.
//
// remove records whether the jar should delete this cookie, as it has already
// expired with respect to now. In this case, e may be incomplete, but it will
// be valid to call e.id (which depends on e's Name, Domain and Path).
//
// A malformed c.Domain will result in an error.
func (j *Jar) newEntry(c *http.Cookie, now time.Time, defPath, host string) (e entry, remove bool, err error) {
	e.Name = c.Name

	if c.Path == "" || c.Path[0] != '/' {
		e.Path = defPath
	} else {
		e.Path = c.Path
	}

	e.Domain, e.HostOnly, err = j.domainAndType(host, c.Domain)
	if err != nil {
		return e, false, err
	}

	// MaxAge takes precedence over Expires.
	if c.MaxAge < 0 {
		return e, true, nil
	} else if c.MaxAge > 0 {
		e.Expires = now.Add(time.Duration(c.MaxAge) * time.Second)
		e.Persistent = true
	} else {
		if c.Expires.IsZero() {
			e.Expires = endOfTime
			e.Persistent = false
		} else {
			if !c.Expires.After(now) {
				return e, true, nil
			}
			e.Expires = c.Expires
			e.Persistent = true
		}
	}

	e.Value = c.Value
	e.Quoted = c.Quoted
	e.Secure = c.Secure
	e.HttpOnly = c.HttpOnly

	switch c.SameSite {
	case http.SameSiteDefaultMode:
		e.SameSite = "SameSite"
	case http.SameSiteStrictMode:
		e.SameSite = "SameSite=Strict"
	case http.SameSiteLaxMode:
		e.SameSite = "SameSite=Lax"
	}

	return e, false, nil
}

var (
	errIllegalDomain   = errors.New("cookiejar: illegal cookie domain attribute")
	errMalformedDomain = errors.New("cookiejar: malformed cookie domain attribute")
)

// endOfTime is the time when session (non-persistent) cookies expire.
// This instant is representable in most date/time formats (not just
// Go's time.Time) and should be far enough in the future.
var endOfTime = time.Date(9999, 12, 31, 23, 59, 59, 0, time.UTC)

// domainAndType determines the cookie's domain and hostOnly attribute.
func (j *Jar) domainAndType(host, domain string) (string, bool, error) {
	if domain == "" {
		// No domain attribute in the SetCookie header indicates a
		// host cookie.
		return host, true, nil
	}

	if isIP(host) {
		// RFC 6265 is not super clear here, a sensible interpretation
		// is that cookies with an IP address in the domain-attribute
		// are allowed.

		// RFC 6265 section 5.2.3 mandates to strip an optional leading
		// dot in the domain-attribute before processing the cookie.
		//
		// Most browsers don't do that for IP addresses, only curl
		// (version 7.54) and IE (version 11) do not reject a
		//     Set-Cookie: a=1; domain=.127.0.0.1
		// This leading dot is optional and serves only as hint for
		// humans to indicate that a cookie with "domain=.bbc.co.uk"
		// would be sent to every subdomain of bbc.co.uk.
		// It just doesn't make sense on IP addresses.
		// The other processing and validation steps in RFC 6265 just
		// collapse to:
		if host != domain {
			return "", false, errIllegalDomain
		}

		// According to RFC 6265 such cookies should be treated as
		// domain cookies.
		// As there are no subdomains of an IP address the treatment
		// according to RFC 6265 would be exactly the same as that of
		// a host-only cookie. Contemporary browsers (and curl) do
		// allows such cookies but treat them as host-only cookies.
		// So do we as it just doesn't make sense to label them as
		// domain cookies when there is no domain; the whole notion of
		// domain cookies requires a domain name to be well defined.
		return host, true, nil
	}

	// From here on: If the cookie is valid, it is a domain cookie (with
	// the one exception of a public suffix below).
	// See RFC 6265 section 5.2.3.
	domain = strings.TrimPrefix(domain, ".")

	if len(domain) == 0 || domain[0] == '.' {
		// Received either "Domain=." or "Domain=..some.thing",
		// both are illegal.
		return "", false, errMalformedDomain
	}

	domain, isASCII := asciiToLower(domain)
	if !isASCII {
		// Received non-ASCII domain, e.g. "perché.com" instead of "xn--perch-fsa.com"
		return "", false, errMalformedDomain
	}

	if domain[len(domain)-1] == '.' {
		// We received stuff like "Domain=www.example.com.".
		// Browsers do handle such stuff (actually differently) but
		// RFC 6265 seems to be clear here (e.g. section 4.1.2.3) in
		// requiring a reject.  4.1.2.3 is not normative, but
		// "Domain Matching" (5.1.3) and "Canonicalized Host Names"
		// (5.1.2) are.
		return "", false, errMalformedDomain
	}

	// See RFC 6265 section 5.3 #5.
	if j.psList != nil {
		if ps := j.psList.PublicSuffix(domain); ps != "" && !hasDotSuffix(domain, ps) {
			if host == domain {
				// This is the one exception in which a cookie
				// with a domain attribute is a host cookie.
				return host, true, nil
			}
			return "", false, errIllegalDomain
		}
	}

	// The domain must domain-match host: www.mycompany.com cannot
	// set cookies for .ourcompetitors.com.
	if host != domain && !hasDotSuffix(host, domain) {
		return "", false, errIllegalDomain
	}

	return domain, false, nil
}

// ───────────── 复制自 net/http/cookiejar/punycode.go ─────────────
// These parameter values are specified in section 5.
//
// All computation is done with int32s, so that overflow behavior is identical
// regardless of whether int is 32-bit or 64-bit.
const (
	base        int32 = 36
	damp        int32 = 700
	initialBias int32 = 72
	initialN    int32 = 128
	skew        int32 = 38
	tmax        int32 = 26
	tmin        int32 = 1
)

// encode encodes a string as specified in section 6.3 and prepends prefix to
// the result.
//
// The "while h < length(input)" line in the specification becomes "for
// remaining != 0" in the Go code, because len(s) in Go is in bytes, not runes.
func encode(prefix, s string) (string, error) {
	output := make([]byte, len(prefix), len(prefix)+1+2*len(s))
	copy(output, prefix)
	delta, n, bias := int32(0), initialN, initialBias
	b, remaining := int32(0), int32(0)
	for _, r := range s {
		if r < utf8.RuneSelf {
			b++
			output = append(output, byte(r))
		} else {
			remaining++
		}
	}
	h := b
	if b > 0 {
		output = append(output, '-')
	}
	for remaining != 0 {
		m := int32(0x7fffffff)
		for _, r := range s {
			if m > r && r >= n {
				m = r
			}
		}
		delta += (m - n) * (h + 1)
		if delta < 0 {
			return "", fmt.Errorf("cookiejar: invalid label %q", s)
		}
		n = m
		for _, r := range s {
			if r < n {
				delta++
				if delta < 0 {
					return "", fmt.Errorf("cookiejar: invalid label %q", s)
				}
				continue
			}
			if r > n {
				continue
			}
			q := delta
			for k := base; ; k += base {
				t := k - bias
				if t < tmin {
					t = tmin
				} else if t > tmax {
					t = tmax
				}
				if q < t {
					break
				}
				output = append(output, encodeDigit(t+(q-t)%(base-t)))
				q = (q - t) / (base - t)
			}
			output = append(output, encodeDigit(q))
			bias = adapt(delta, h+1, h == b)
			delta = 0
			h++
			remaining--
		}
		delta++
		n++
	}
	return string(output), nil
}

func encodeDigit(digit int32) byte {
	switch {
	case 0 <= digit && digit < 26:
		return byte(digit + 'a')
	case 26 <= digit && digit < 36:
		return byte(digit + ('0' - 26))
	}
	panic("cookiejar: internal error in punycode encoding")
}

// adapt is the bias adaptation function specified in section 6.1.
func adapt(delta, numPoints int32, firstTime bool) int32 {
	if firstTime {
		delta /= damp
	} else {
		delta /= 2
	}
	delta += delta / numPoints
	k := int32(0)
	for delta > ((base-tmin)*tmax)/2 {
		delta /= base - tmin
		k += base
	}
	return k + (base-tmin+1)*delta/(delta+skew)
}

// Strictly speaking, the remaining code below deals with IDNA (RFC 5890 and
// friends) and not Punycode (RFC 3492) per se.

// acePrefix is the ASCII Compatible Encoding prefix.
const acePrefix = "xn--"

// toASCII converts a domain or domain label to its ASCII form. For example,
// toASCII("bücher.example.com") is "xn--bcher-kva.example.com", and
// toASCII("golang") is "golang".
func toASCII(s string) (string, error) {
	if asciiIs(s) {
		return s, nil
	}
	labels := strings.Split(s, ".")
	for i, label := range labels {
		if !asciiIs(label) {
			a, err := encode(acePrefix, label)
			if err != nil {
				return "", err
			}
			labels[i] = a
		}
	}
	return strings.Join(labels, "."), nil
}

// ───────────── 测试表：复制自 jar_test.go / punycode_test.go ─────────────
var hasDotSuffixTests = [...]struct {
	s, suffix string
}{
	{"", ""},
	{"", "."},
	{"", "x"},
	{".", ""},
	{".", "."},
	{".", ".."},
	{".", "x"},
	{".", "x."},
	{".", ".x"},
	{".", ".x."},
	{"x", ""},
	{"x", "."},
	{"x", ".."},
	{"x", "x"},
	{"x", "x."},
	{"x", ".x"},
	{"x", ".x."},
	{".x", ""},
	{".x", "."},
	{".x", ".."},
	{".x", "x"},
	{".x", "x."},
	{".x", ".x"},
	{".x", ".x."},
	{"x.", ""},
	{"x.", "."},
	{"x.", ".."},
	{"x.", "x"},
	{"x.", "x."},
	{"x.", ".x"},
	{"x.", ".x."},
	{"com", ""},
	{"com", "m"},
	{"com", "om"},
	{"com", "com"},
	{"com", ".com"},
	{"com", "x.com"},
	{"com", "xcom"},
	{"com", "xorg"},
	{"com", "org"},
	{"com", "rg"},
	{"foo.com", ""},
	{"foo.com", "m"},
	{"foo.com", "om"},
	{"foo.com", "com"},
	{"foo.com", ".com"},
	{"foo.com", "o.com"},
	{"foo.com", "oo.com"},
	{"foo.com", "foo.com"},
	{"foo.com", ".foo.com"},
	{"foo.com", "x.foo.com"},
	{"foo.com", "xfoo.com"},
	{"foo.com", "xfoo.org"},
	{"foo.com", "foo.org"},
	{"foo.com", "oo.org"},
	{"foo.com", "o.org"},
	{"foo.com", ".org"},
	{"foo.com", "org"},
	{"foo.com", "rg"},
}
var basicsTests = [...]jarTest{
	{
		"Retrieval of a plain host cookie.",
		"http://www.host.test/",
		[]string{"A=a"},
		"A=a",
		[]query{
			{"http://www.host.test", "A=a"},
			{"http://www.host.test/", "A=a"},
			{"http://www.host.test/some/path", "A=a"},
			{"https://www.host.test", "A=a"},
			{"https://www.host.test/", "A=a"},
			{"https://www.host.test/some/path", "A=a"},
			{"ftp://www.host.test", ""},
			{"ftp://www.host.test/", ""},
			{"ftp://www.host.test/some/path", ""},
			{"http://www.other.org", ""},
			{"http://sibling.host.test", ""},
			{"http://deep.www.host.test", ""},
		},
	},
	{
		"Secure cookies are not returned to http.",
		"http://www.host.test/",
		[]string{"A=a; secure"},
		"A=a",
		[]query{
			{"http://www.host.test", ""},
			{"http://www.host.test/", ""},
			{"http://www.host.test/some/path", ""},
			{"https://www.host.test", "A=a"},
			{"https://www.host.test/", "A=a"},
			{"https://www.host.test/some/path", "A=a"},
		},
	},
	{
		"Secure cookies are sent for localhost",
		"http://localhost:8910/",
		[]string{"A=a; secure"},
		"A=a",
		[]query{
			{"http://localhost:8910", "A=a"},
			{"http://localhost:8910/", "A=a"},
			{"http://localhost:8910/some/path", "A=a"},
			{"https://localhost:8910", "A=a"},
			{"https://localhost:8910/", "A=a"},
			{"https://localhost:8910/some/path", "A=a"},
		},
	},
	{
		"Secure cookies are sent for localhost (tld)",
		"http://example.LOCALHOST:8910/",
		[]string{"A=a; secure"},
		"A=a",
		[]query{
			{"http://example.LOCALHOST:8910", "A=a"},
			{"http://example.LOCALHOST:8910/", "A=a"},
			{"http://example.LOCALHOST:8910/some/path", "A=a"},
			{"https://example.LOCALHOST:8910", "A=a"},
			{"https://example.LOCALHOST:8910/", "A=a"},
			{"https://example.LOCALHOST:8910/some/path", "A=a"},
		},
	},
	{
		"Secure cookies are sent for localhost (ipv6)",
		"http://[::1]:8910/",
		[]string{"A=a; secure"},
		"A=a",
		[]query{
			{"http://[::1]:8910", "A=a"},
			{"http://[::1]:8910/", "A=a"},
			{"http://[::1]:8910/some/path", "A=a"},
			{"https://[::1]:8910", "A=a"},
			{"https://[::1]:8910/", "A=a"},
			{"https://[::1]:8910/some/path", "A=a"},
		},
	},
	{
		"Localhost only if it's a segment",
		"http://notlocalhost/",
		[]string{"A=a; secure"},
		"A=a",
		[]query{
			{"http://notlocalhost", ""},
			{"http://notlocalhost/", ""},
			{"http://notlocalhost/some/path", ""},
			{"https://notlocalhost", "A=a"},
			{"https://notlocalhost/", "A=a"},
			{"https://notlocalhost/some/path", "A=a"},
		},
	},
	{
		"Explicit path.",
		"http://www.host.test/",
		[]string{"A=a; path=/some/path"},
		"A=a",
		[]query{
			{"http://www.host.test", ""},
			{"http://www.host.test/", ""},
			{"http://www.host.test/some", ""},
			{"http://www.host.test/some/", ""},
			{"http://www.host.test/some/path", "A=a"},
			{"http://www.host.test/some/paths", ""},
			{"http://www.host.test/some/path/foo", "A=a"},
			{"http://www.host.test/some/path/foo/", "A=a"},
		},
	},
	{
		"Implicit path #1: path is a directory.",
		"http://www.host.test/some/path/",
		[]string{"A=a"},
		"A=a",
		[]query{
			{"http://www.host.test", ""},
			{"http://www.host.test/", ""},
			{"http://www.host.test/some", ""},
			{"http://www.host.test/some/", ""},
			{"http://www.host.test/some/path", "A=a"},
			{"http://www.host.test/some/paths", ""},
			{"http://www.host.test/some/path/foo", "A=a"},
			{"http://www.host.test/some/path/foo/", "A=a"},
		},
	},
	{
		"Implicit path #2: path is not a directory.",
		"http://www.host.test/some/path/index.html",
		[]string{"A=a"},
		"A=a",
		[]query{
			{"http://www.host.test", ""},
			{"http://www.host.test/", ""},
			{"http://www.host.test/some", ""},
			{"http://www.host.test/some/", ""},
			{"http://www.host.test/some/path", "A=a"},
			{"http://www.host.test/some/paths", ""},
			{"http://www.host.test/some/path/foo", "A=a"},
			{"http://www.host.test/some/path/foo/", "A=a"},
		},
	},
	{
		"Implicit path #3: no path in URL at all.",
		"http://www.host.test",
		[]string{"A=a"},
		"A=a",
		[]query{
			{"http://www.host.test", "A=a"},
			{"http://www.host.test/", "A=a"},
			{"http://www.host.test/some/path", "A=a"},
		},
	},
	{
		"Cookies are sorted by path length.",
		"http://www.host.test/",
		[]string{
			"A=a; path=/foo/bar",
			"B=b; path=/foo/bar/baz/qux",
			"C=c; path=/foo/bar/baz",
			"D=d; path=/foo"},
		"A=a B=b C=c D=d",
		[]query{
			{"http://www.host.test/foo/bar/baz/qux", "B=b C=c A=a D=d"},
			{"http://www.host.test/foo/bar/baz/", "C=c A=a D=d"},
			{"http://www.host.test/foo/bar", "A=a D=d"},
		},
	},
	{
		"Creation time determines sorting on same length paths.",
		"http://www.host.test/",
		[]string{
			"A=a; path=/foo/bar",
			"X=x; path=/foo/bar",
			"Y=y; path=/foo/bar/baz/qux",
			"B=b; path=/foo/bar/baz/qux",
			"C=c; path=/foo/bar/baz",
			"W=w; path=/foo/bar/baz",
			"Z=z; path=/foo",
			"D=d; path=/foo"},
		"A=a B=b C=c D=d W=w X=x Y=y Z=z",
		[]query{
			{"http://www.host.test/foo/bar/baz/qux", "Y=y B=b C=c W=w A=a X=x Z=z D=d"},
			{"http://www.host.test/foo/bar/baz/", "C=c W=w A=a X=x Z=z D=d"},
			{"http://www.host.test/foo/bar", "A=a X=x Z=z D=d"},
		},
	},
	{
		"Sorting of same-name cookies.",
		"http://www.host.test/",
		[]string{
			"A=1; path=/",
			"A=2; path=/path",
			"A=3; path=/quux",
			"A=4; path=/path/foo",
			"A=5; domain=.host.test; path=/path",
			"A=6; domain=.host.test; path=/quux",
			"A=7; domain=.host.test; path=/path/foo",
		},
		"A=1 A=2 A=3 A=4 A=5 A=6 A=7",
		[]query{
			{"http://www.host.test/path", "A=2 A=5 A=1"},
			{"http://www.host.test/path/foo", "A=4 A=7 A=2 A=5 A=1"},
		},
	},
	{
		"Disallow domain cookie on public suffix.",
		"http://www.bbc.co.uk",
		[]string{
			"a=1",
			"b=2; domain=co.uk",
		},
		"a=1",
		[]query{{"http://www.bbc.co.uk", "a=1"}},
	},
	{
		"Host cookie on IP.",
		"http://192.168.0.10",
		[]string{"a=1"},
		"a=1",
		[]query{{"http://192.168.0.10", "a=1"}},
	},
	{
		"Domain cookies on IP.",
		"http://192.168.0.10",
		[]string{
			"a=1; domain=192.168.0.10",  // allowed
			"b=2; domain=172.31.9.9",    // rejected, can't set cookie for other IP
			"c=3; domain=.192.168.0.10", // rejected like in most browsers
		},
		"a=1",
		[]query{
			{"http://192.168.0.10", "a=1"},
			{"http://172.31.9.9", ""},
			{"http://www.fancy.192.168.0.10", ""},
		},
	},
	{
		"Port is ignored #1.",
		"http://www.host.test/",
		[]string{"a=1"},
		"a=1",
		[]query{
			{"http://www.host.test", "a=1"},
			{"http://www.host.test:8080/", "a=1"},
		},
	},
	{
		"Port is ignored #2.",
		"http://www.host.test:8080/",
		[]string{"a=1"},
		"a=1",
		[]query{
			{"http://www.host.test", "a=1"},
			{"http://www.host.test:8080/", "a=1"},
			{"http://www.host.test:1234/", "a=1"},
		},
	},
	{
		"IPv6 zone is not treated as a host.",
		"https://example.com/",
		[]string{"a=1"},
		"a=1",
		[]query{
			{"https://[::1%25.example.com]:80/", ""},
		},
	},
	{
		"Retrieval of cookies with quoted values", // issue #46443
		"http://www.host.test/",
		[]string{
			`cookie-1="quoted"`,
			`cookie-2="quoted with spaces"`,
			`cookie-3="quoted,with,commas"`,
			`cookie-4= ,`,
		},
		`cookie-1="quoted" cookie-2="quoted with spaces" cookie-3="quoted,with,commas" cookie-4=" ,"`,
		[]query{
			{
				"http://www.host.test",
				`cookie-1="quoted" cookie-2="quoted with spaces" cookie-3="quoted,with,commas" cookie-4=" ,"`,
			},
		},
	},
}
var updateAndDeleteTests = [...]jarTest{
	{
		"Set initial cookies.",
		"http://www.host.test",
		[]string{
			"a=1",
			"b=2; secure",
			"c=3; httponly",
			"d=4; secure; httponly"},
		"a=1 b=2 c=3 d=4",
		[]query{
			{"http://www.host.test", "a=1 c=3"},
			{"https://www.host.test", "a=1 b=2 c=3 d=4"},
		},
	},
	{
		"Update value via http.",
		"http://www.host.test",
		[]string{
			"a=w",
			"b=x; secure",
			"c=y; httponly",
			"d=z; secure; httponly"},
		"a=w b=x c=y d=z",
		[]query{
			{"http://www.host.test", "a=w c=y"},
			{"https://www.host.test", "a=w b=x c=y d=z"},
		},
	},
	{
		"Clear Secure flag from an http.",
		"http://www.host.test/",
		[]string{
			"b=xx",
			"d=zz; httponly"},
		"a=w b=xx c=y d=zz",
		[]query{{"http://www.host.test", "a=w b=xx c=y d=zz"}},
	},
	{
		"Delete all.",
		"http://www.host.test/",
		[]string{
			"a=1; max-Age=-1",                    // delete via MaxAge
			"b=2; " + expiresIn(-10),             // delete via Expires
			"c=2; max-age=-1; " + expiresIn(-10), // delete via both
			"d=4; max-age=-1; " + expiresIn(10)}, // MaxAge takes precedence
		"",
		[]query{{"http://www.host.test", ""}},
	},
	{
		"Refill #1.",
		"http://www.host.test",
		[]string{
			"A=1",
			"A=2; path=/foo",
			"A=3; domain=.host.test",
			"A=4; path=/foo; domain=.host.test"},
		"A=1 A=2 A=3 A=4",
		[]query{{"http://www.host.test/foo", "A=2 A=4 A=1 A=3"}},
	},
	{
		"Refill #2.",
		"http://www.google.com",
		[]string{
			"A=6",
			"A=7; path=/foo",
			"A=8; domain=.google.com",
			"A=9; path=/foo; domain=.google.com"},
		"A=1 A=2 A=3 A=4 A=6 A=7 A=8 A=9",
		[]query{
			{"http://www.host.test/foo", "A=2 A=4 A=1 A=3"},
			{"http://www.google.com/foo", "A=7 A=9 A=6 A=8"},
		},
	},
	{
		"Delete A7.",
		"http://www.google.com",
		[]string{"A=; path=/foo; max-age=-1"},
		"A=1 A=2 A=3 A=4 A=6 A=8 A=9",
		[]query{
			{"http://www.host.test/foo", "A=2 A=4 A=1 A=3"},
			{"http://www.google.com/foo", "A=9 A=6 A=8"},
		},
	},
	{
		"Delete A4.",
		"http://www.host.test",
		[]string{"A=; path=/foo; domain=host.test; max-age=-1"},
		"A=1 A=2 A=3 A=6 A=8 A=9",
		[]query{
			{"http://www.host.test/foo", "A=2 A=1 A=3"},
			{"http://www.google.com/foo", "A=9 A=6 A=8"},
		},
	},
	{
		"Delete A6.",
		"http://www.google.com",
		[]string{"A=; max-age=-1"},
		"A=1 A=2 A=3 A=8 A=9",
		[]query{
			{"http://www.host.test/foo", "A=2 A=1 A=3"},
			{"http://www.google.com/foo", "A=9 A=8"},
		},
	},
	{
		"Delete A3.",
		"http://www.host.test",
		[]string{"A=; domain=host.test; max-age=-1"},
		"A=1 A=2 A=8 A=9",
		[]query{
			{"http://www.host.test/foo", "A=2 A=1"},
			{"http://www.google.com/foo", "A=9 A=8"},
		},
	},
	{
		"No cross-domain delete.",
		"http://www.host.test",
		[]string{
			"A=; domain=google.com; max-age=-1",
			"A=; path=/foo; domain=google.com; max-age=-1"},
		"A=1 A=2 A=8 A=9",
		[]query{
			{"http://www.host.test/foo", "A=2 A=1"},
			{"http://www.google.com/foo", "A=9 A=8"},
		},
	},
	{
		"Delete A8 and A9.",
		"http://www.google.com",
		[]string{
			"A=; domain=google.com; max-age=-1",
			"A=; path=/foo; domain=google.com; max-age=-1"},
		"A=1 A=2",
		[]query{
			{"http://www.host.test/foo", "A=2 A=1"},
			{"http://www.google.com/foo", ""},
		},
	},
}
var chromiumBasicsTests = [...]jarTest{
	{
		"DomainWithTrailingDotTest.",
		"http://www.google.com/",
		[]string{
			"a=1; domain=.www.google.com.",
			"b=2; domain=.www.google.com.."},
		"",
		[]query{
			{"http://www.google.com", ""},
		},
	},
	{
		"ValidSubdomainTest #1.",
		"http://a.b.c.d.com",
		[]string{
			"a=1; domain=.a.b.c.d.com",
			"b=2; domain=.b.c.d.com",
			"c=3; domain=.c.d.com",
			"d=4; domain=.d.com"},
		"a=1 b=2 c=3 d=4",
		[]query{
			{"http://a.b.c.d.com", "a=1 b=2 c=3 d=4"},
			{"http://b.c.d.com", "b=2 c=3 d=4"},
			{"http://c.d.com", "c=3 d=4"},
			{"http://d.com", "d=4"},
		},
	},
	{
		"ValidSubdomainTest #2.",
		"http://a.b.c.d.com",
		[]string{
			"a=1; domain=.a.b.c.d.com",
			"b=2; domain=.b.c.d.com",
			"c=3; domain=.c.d.com",
			"d=4; domain=.d.com",
			"X=bcd; domain=.b.c.d.com",
			"X=cd; domain=.c.d.com"},
		"X=bcd X=cd a=1 b=2 c=3 d=4",
		[]query{
			{"http://b.c.d.com", "b=2 c=3 d=4 X=bcd X=cd"},
			{"http://c.d.com", "c=3 d=4 X=cd"},
		},
	},
	{
		"InvalidDomainTest #1.",
		"http://foo.bar.com",
		[]string{
			"a=1; domain=.yo.foo.bar.com",
			"b=2; domain=.foo.com",
			"c=3; domain=.bar.foo.com",
			"d=4; domain=.foo.bar.com.net",
			"e=5; domain=ar.com",
			"f=6; domain=.",
			"g=7; domain=/",
			"h=8; domain=http://foo.bar.com",
			"i=9; domain=..foo.bar.com",
			"j=10; domain=..bar.com",
			"k=11; domain=.foo.bar.com?blah",
			"l=12; domain=.foo.bar.com/blah",
			"m=12; domain=.foo.bar.com:80",
			"n=14; domain=.foo.bar.com:",
			"o=15; domain=.foo.bar.com#sup",
		},
		"", // Jar is empty.
		[]query{{"http://foo.bar.com", ""}},
	},
	{
		"InvalidDomainTest #2.",
		"http://foo.com.com",
		[]string{"a=1; domain=.foo.com.com.com"},
		"",
		[]query{{"http://foo.bar.com", ""}},
	},
	{
		"DomainWithoutLeadingDotTest #1.",
		"http://manage.hosted.filefront.com",
		[]string{"a=1; domain=filefront.com"},
		"a=1",
		[]query{{"http://www.filefront.com", "a=1"}},
	},
	{
		"DomainWithoutLeadingDotTest #2.",
		"http://www.google.com",
		[]string{"a=1; domain=www.google.com"},
		"a=1",
		[]query{
			{"http://www.google.com", "a=1"},
			{"http://sub.www.google.com", "a=1"},
			{"http://something-else.com", ""},
		},
	},
	{
		"CaseInsensitiveDomainTest.",
		"http://www.google.com",
		[]string{
			"a=1; domain=.GOOGLE.COM",
			"b=2; domain=.www.gOOgLE.coM"},
		"a=1 b=2",
		[]query{{"http://www.google.com", "a=1 b=2"}},
	},
	{
		"TestIpAddress #1.",
		"http://1.2.3.4/foo",
		[]string{"a=1; path=/"},
		"a=1",
		[]query{{"http://1.2.3.4/foo", "a=1"}},
	},
	{
		"TestIpAddress #2.",
		"http://1.2.3.4/foo",
		[]string{
			"a=1; domain=.1.2.3.4",
			"b=2; domain=.3.4"},
		"",
		[]query{{"http://1.2.3.4/foo", ""}},
	},
	{
		"TestIpAddress #3.",
		"http://1.2.3.4/foo",
		[]string{"a=1; domain=1.2.3.3"},
		"",
		[]query{{"http://1.2.3.4/foo", ""}},
	},
	{
		"TestIpAddress #4.",
		"http://1.2.3.4/foo",
		[]string{"a=1; domain=1.2.3.4"},
		"a=1",
		[]query{{"http://1.2.3.4/foo", "a=1"}},
	},
	{
		"TestNonDottedAndTLD #2.",
		"http://com./index.html",
		[]string{"a=1"},
		"a=1",
		[]query{
			{"http://com./index.html", "a=1"},
			{"http://no-cookies.com./index.html", ""},
		},
	},
	{
		"TestNonDottedAndTLD #3.",
		"http://a.b",
		[]string{
			"a=1; domain=.b",
			"b=2; domain=b"},
		"",
		[]query{{"http://bar.foo", ""}},
	},
	{
		"TestNonDottedAndTLD #4.",
		"http://google.com",
		[]string{
			"a=1; domain=.com",
			"b=2; domain=com"},
		"",
		[]query{{"http://google.com", ""}},
	},
	{
		"TestNonDottedAndTLD #5.",
		"http://google.co.uk",
		[]string{
			"a=1; domain=.co.uk",
			"b=2; domain=.uk"},
		"",
		[]query{
			{"http://google.co.uk", ""},
			{"http://else.co.com", ""},
			{"http://else.uk", ""},
		},
	},
	{
		"TestHostEndsWithDot.",
		"http://www.google.com",
		[]string{
			"a=1",
			"b=2; domain=.www.google.com."},
		"a=1",
		[]query{{"http://www.google.com", "a=1"}},
	},
	{
		"PathTest",
		"http://www.google.izzle",
		[]string{"a=1; path=/wee"},
		"a=1",
		[]query{
			{"http://www.google.izzle/wee", "a=1"},
			{"http://www.google.izzle/wee/", "a=1"},
			{"http://www.google.izzle/wee/war", "a=1"},
			{"http://www.google.izzle/wee/war/more/more", "a=1"},
			{"http://www.google.izzle/weehee", ""},
			{"http://www.google.izzle/", ""},
		},
	},
}
var chromiumDomainTests = [...]jarTest{
	{
		"Fill #1.",
		"http://www.google.izzle",
		[]string{"A=B"},
		"A=B",
		[]query{{"http://www.google.izzle", "A=B"}},
	},
	{
		"Fill #2.",
		"http://www.google.izzle",
		[]string{"C=D; domain=.google.izzle"},
		"A=B C=D",
		[]query{{"http://www.google.izzle", "A=B C=D"}},
	},
	{
		"Verify A is a host cookie and not accessible from subdomain.",
		"http://unused.nil",
		[]string{},
		"A=B C=D",
		[]query{{"http://foo.www.google.izzle", "C=D"}},
	},
	{
		"Verify domain cookies are found on proper domain.",
		"http://www.google.izzle",
		[]string{"E=F; domain=.www.google.izzle"},
		"A=B C=D E=F",
		[]query{{"http://www.google.izzle", "A=B C=D E=F"}},
	},
	{
		"Leading dots in domain attributes are optional.",
		"http://www.google.izzle",
		[]string{"G=H; domain=www.google.izzle"},
		"A=B C=D E=F G=H",
		[]query{{"http://www.google.izzle", "A=B C=D E=F G=H"}},
	},
	{
		"Verify domain enforcement works #1.",
		"http://www.google.izzle",
		[]string{"K=L; domain=.bar.www.google.izzle"},
		"A=B C=D E=F G=H",
		[]query{{"http://bar.www.google.izzle", "C=D E=F G=H"}},
	},
	{
		"Verify domain enforcement works #2.",
		"http://unused.nil",
		[]string{},
		"A=B C=D E=F G=H",
		[]query{{"http://www.google.izzle", "A=B C=D E=F G=H"}},
	},
}
var chromiumDeletionTests = [...]jarTest{
	{
		"Create session cookie a1.",
		"http://www.google.com",
		[]string{"a=1"},
		"a=1",
		[]query{{"http://www.google.com", "a=1"}},
	},
	{
		"Delete sc a1 via MaxAge.",
		"http://www.google.com",
		[]string{"a=1; max-age=-1"},
		"",
		[]query{{"http://www.google.com", ""}},
	},
	{
		"Create session cookie b2.",
		"http://www.google.com",
		[]string{"b=2"},
		"b=2",
		[]query{{"http://www.google.com", "b=2"}},
	},
	{
		"Delete sc b2 via Expires.",
		"http://www.google.com",
		[]string{"b=2; " + expiresIn(-10)},
		"",
		[]query{{"http://www.google.com", ""}},
	},
	{
		"Create persistent cookie c3.",
		"http://www.google.com",
		[]string{"c=3; max-age=3600"},
		"c=3",
		[]query{{"http://www.google.com", "c=3"}},
	},
	{
		"Delete pc c3 via MaxAge.",
		"http://www.google.com",
		[]string{"c=3; max-age=-1"},
		"",
		[]query{{"http://www.google.com", ""}},
	},
	{
		"Create persistent cookie d4.",
		"http://www.google.com",
		[]string{"d=4; max-age=3600"},
		"d=4",
		[]query{{"http://www.google.com", "d=4"}},
	},
	{
		"Delete pc d4 via Expires.",
		"http://www.google.com",
		[]string{"d=4; " + expiresIn(-10)},
		"",
		[]query{{"http://www.google.com", ""}},
	},
}
var domainHandlingTests = [...]jarTest{
	{
		"Host cookie",
		"http://www.host.test",
		[]string{"a=1"},
		"a=1",
		[]query{
			{"http://www.host.test", "a=1"},
			{"http://host.test", ""},
			{"http://bar.host.test", ""},
			{"http://foo.www.host.test", ""},
			{"http://other.test", ""},
			{"http://test", ""},
		},
	},
	{
		"Domain cookie #1",
		"http://www.host.test",
		[]string{"a=1; domain=host.test"},
		"a=1",
		[]query{
			{"http://www.host.test", "a=1"},
			{"http://host.test", "a=1"},
			{"http://bar.host.test", "a=1"},
			{"http://foo.www.host.test", "a=1"},
			{"http://other.test", ""},
			{"http://test", ""},
		},
	},
	{
		"Domain cookie #2",
		"http://www.host.test",
		[]string{"a=1; domain=.host.test"},
		"a=1",
		[]query{
			{"http://www.host.test", "a=1"},
			{"http://host.test", "a=1"},
			{"http://bar.host.test", "a=1"},
			{"http://foo.www.host.test", "a=1"},
			{"http://other.test", ""},
			{"http://test", ""},
		},
	},
	{
		"Host cookie on IDNA domain #1",
		"http://www.bücher.test",
		[]string{"a=1"},
		"a=1",
		[]query{
			{"http://www.bücher.test", "a=1"},
			{"http://www.xn--bcher-kva.test", "a=1"},
			{"http://bücher.test", ""},
			{"http://xn--bcher-kva.test", ""},
			{"http://bar.bücher.test", ""},
			{"http://bar.xn--bcher-kva.test", ""},
			{"http://foo.www.bücher.test", ""},
			{"http://foo.www.xn--bcher-kva.test", ""},
			{"http://other.test", ""},
			{"http://test", ""},
		},
	},
	{
		"Host cookie on IDNA domain #2",
		"http://www.xn--bcher-kva.test",
		[]string{"a=1"},
		"a=1",
		[]query{
			{"http://www.bücher.test", "a=1"},
			{"http://www.xn--bcher-kva.test", "a=1"},
			{"http://bücher.test", ""},
			{"http://xn--bcher-kva.test", ""},
			{"http://bar.bücher.test", ""},
			{"http://bar.xn--bcher-kva.test", ""},
			{"http://foo.www.bücher.test", ""},
			{"http://foo.www.xn--bcher-kva.test", ""},
			{"http://other.test", ""},
			{"http://test", ""},
		},
	},
	{
		"Domain cookie on IDNA domain #1",
		"http://www.bücher.test",
		[]string{"a=1; domain=xn--bcher-kva.test"},
		"a=1",
		[]query{
			{"http://www.bücher.test", "a=1"},
			{"http://www.xn--bcher-kva.test", "a=1"},
			{"http://bücher.test", "a=1"},
			{"http://xn--bcher-kva.test", "a=1"},
			{"http://bar.bücher.test", "a=1"},
			{"http://bar.xn--bcher-kva.test", "a=1"},
			{"http://foo.www.bücher.test", "a=1"},
			{"http://foo.www.xn--bcher-kva.test", "a=1"},
			{"http://other.test", ""},
			{"http://test", ""},
		},
	},
	{
		"Domain cookie on IDNA domain #2",
		"http://www.xn--bcher-kva.test",
		[]string{"a=1; domain=xn--bcher-kva.test"},
		"a=1",
		[]query{
			{"http://www.bücher.test", "a=1"},
			{"http://www.xn--bcher-kva.test", "a=1"},
			{"http://bücher.test", "a=1"},
			{"http://xn--bcher-kva.test", "a=1"},
			{"http://bar.bücher.test", "a=1"},
			{"http://bar.xn--bcher-kva.test", "a=1"},
			{"http://foo.www.bücher.test", "a=1"},
			{"http://foo.www.xn--bcher-kva.test", "a=1"},
			{"http://other.test", ""},
			{"http://test", ""},
		},
	},
	{
		"Host cookie on TLD.",
		"http://com",
		[]string{"a=1"},
		"a=1",
		[]query{
			{"http://com", "a=1"},
			{"http://any.com", ""},
			{"http://any.test", ""},
		},
	},
	{
		"Domain cookie on TLD becomes a host cookie.",
		"http://com",
		[]string{"a=1; domain=com"},
		"a=1",
		[]query{
			{"http://com", "a=1"},
			{"http://any.com", ""},
			{"http://any.test", ""},
		},
	},
	{
		"Host cookie on public suffix.",
		"http://co.uk",
		[]string{"a=1"},
		"a=1",
		[]query{
			{"http://co.uk", "a=1"},
			{"http://uk", ""},
			{"http://some.co.uk", ""},
			{"http://foo.some.co.uk", ""},
			{"http://any.uk", ""},
		},
	},
	{
		"Domain cookie on public suffix is ignored.",
		"http://some.co.uk",
		[]string{"a=1; domain=co.uk"},
		"",
		[]query{
			{"http://co.uk", ""},
			{"http://uk", ""},
			{"http://some.co.uk", ""},
			{"http://foo.some.co.uk", ""},
			{"http://any.uk", ""},
		},
	},
}
var punycodeTestCases = [...]struct {
	s, encoded string
}{
	{"", ""},
	{"-", "--"},
	{"-a", "-a-"},
	{"-a-", "-a--"},
	{"a", "a-"},
	{"a-", "a--"},
	{"a-b", "a-b-"},
	{"books", "books-"},
	{"bücher", "bcher-kva"},
	{"Hello世界", "Hello-ck1hg65u"},
	{"ü", "tda"},
	{"üý", "tdac"},

	// The test cases below come from RFC 3492 section 7.1 with Errata 3026.
	{
		// (A) Arabic (Egyptian).
		"\u0644\u064A\u0647\u0645\u0627\u0628\u062A\u0643\u0644" +
			"\u0645\u0648\u0634\u0639\u0631\u0628\u064A\u061F",
		"egbpdaj6bu4bxfgehfvwxn",
	},
	{
		// (B) Chinese (simplified).
		"\u4ED6\u4EEC\u4E3A\u4EC0\u4E48\u4E0D\u8BF4\u4E2D\u6587",
		"ihqwcrb4cv8a8dqg056pqjye",
	},
	{
		// (C) Chinese (traditional).
		"\u4ED6\u5011\u7232\u4EC0\u9EBD\u4E0D\u8AAA\u4E2D\u6587",
		"ihqwctvzc91f659drss3x8bo0yb",
	},
	{
		// (D) Czech.
		"\u0050\u0072\u006F\u010D\u0070\u0072\u006F\u0073\u0074" +
			"\u011B\u006E\u0065\u006D\u006C\u0075\u0076\u00ED\u010D" +
			"\u0065\u0073\u006B\u0079",
		"Proprostnemluvesky-uyb24dma41a",
	},
	{
		// (E) Hebrew.
		"\u05DC\u05DE\u05D4\u05D4\u05DD\u05E4\u05E9\u05D5\u05D8" +
			"\u05DC\u05D0\u05DE\u05D3\u05D1\u05E8\u05D9\u05DD\u05E2" +
			"\u05D1\u05E8\u05D9\u05EA",
		"4dbcagdahymbxekheh6e0a7fei0b",
	},
	{
		// (F) Hindi (Devanagari).
		"\u092F\u0939\u0932\u094B\u0917\u0939\u093F\u0928\u094D" +
			"\u0926\u0940\u0915\u094D\u092F\u094B\u0902\u0928\u0939" +
			"\u0940\u0902\u092C\u094B\u0932\u0938\u0915\u0924\u0947" +
			"\u0939\u0948\u0902",
		"i1baa7eci9glrd9b2ae1bj0hfcgg6iyaf8o0a1dig0cd",
	},
	{
		// (G) Japanese (kanji and hiragana).
		"\u306A\u305C\u307F\u3093\u306A\u65E5\u672C\u8A9E\u3092" +
			"\u8A71\u3057\u3066\u304F\u308C\u306A\u3044\u306E\u304B",
		"n8jok5ay5dzabd5bym9f0cm5685rrjetr6pdxa",
	},
	{
		// (H) Korean (Hangul syllables).
		"\uC138\uACC4\uC758\uBAA8\uB4E0\uC0AC\uB78C\uB4E4\uC774" +
			"\uD55C\uAD6D\uC5B4\uB97C\uC774\uD574\uD55C\uB2E4\uBA74" +
			"\uC5BC\uB9C8\uB098\uC88B\uC744\uAE4C",
		"989aomsvi5e83db1d2a355cv1e0vak1dwrv93d5xbh15a0dt30a5j" +
			"psd879ccm6fea98c",
	},
	{
		// (I) Russian (Cyrillic).
		"\u043F\u043E\u0447\u0435\u043C\u0443\u0436\u0435\u043E" +
			"\u043D\u0438\u043D\u0435\u0433\u043E\u0432\u043E\u0440" +
			"\u044F\u0442\u043F\u043E\u0440\u0443\u0441\u0441\u043A" +
			"\u0438",
		"b1abfaaepdrnnbgefbadotcwatmq2g4l",
	},
	{
		// (J) Spanish.
		"\u0050\u006F\u0072\u0071\u0075\u00E9\u006E\u006F\u0070" +
			"\u0075\u0065\u0064\u0065\u006E\u0073\u0069\u006D\u0070" +
			"\u006C\u0065\u006D\u0065\u006E\u0074\u0065\u0068\u0061" +
			"\u0062\u006C\u0061\u0072\u0065\u006E\u0045\u0073\u0070" +
			"\u0061\u00F1\u006F\u006C",
		"PorqunopuedensimplementehablarenEspaol-fmd56a",
	},
	{
		// (K) Vietnamese.
		"\u0054\u1EA1\u0069\u0073\u0061\u006F\u0068\u1ECD\u006B" +
			"\u0068\u00F4\u006E\u0067\u0074\u0068\u1EC3\u0063\u0068" +
			"\u1EC9\u006E\u00F3\u0069\u0074\u0069\u1EBF\u006E\u0067" +
			"\u0056\u0069\u1EC7\u0074",
		"TisaohkhngthchnitingVit-kjcr8268qyxafd2f1b9g",
	},
	{
		// (L) 3<nen>B<gumi><kinpachi><sensei>.
		"\u0033\u5E74\u0042\u7D44\u91D1\u516B\u5148\u751F",
		"3B-ww4c5e180e575a65lsy2b",
	},
	{
		// (M) <amuro><namie>-with-SUPER-MONKEYS.
		"\u5B89\u5BA4\u5948\u7F8E\u6075\u002D\u0077\u0069\u0074" +
			"\u0068\u002D\u0053\u0055\u0050\u0045\u0052\u002D\u004D" +
			"\u004F\u004E\u004B\u0045\u0059\u0053",
		"-with-SUPER-MONKEYS-pc58ag80a8qai00g7n9n",
	},
	{
		// (N) Hello-Another-Way-<sorezore><no><basho>.
		"\u0048\u0065\u006C\u006C\u006F\u002D\u0041\u006E\u006F" +
			"\u0074\u0068\u0065\u0072\u002D\u0057\u0061\u0079\u002D" +
			"\u305D\u308C\u305E\u308C\u306E\u5834\u6240",
		"Hello-Another-Way--fc4qua05auwb3674vfr0b",
	},
	{
		// (O) <hitotsu><yane><no><shita>2.
		"\u3072\u3068\u3064\u5C4B\u6839\u306E\u4E0B\u0032",
		"2-u9tlzr9756bt3uc0v",
	},
	{
		// (P) Maji<de>Koi<suru>5<byou><mae>
		"\u004D\u0061\u006A\u0069\u3067\u004B\u006F\u0069\u3059" +
			"\u308B\u0035\u79D2\u524D",
		"MajiKoi5-783gue6qz075azm5e",
	},
	{
		// (Q) <pafii>de<runba>
		"\u30D1\u30D5\u30A3\u30FC\u0064\u0065\u30EB\u30F3\u30D0",
		"de-jg4avhby1noc0d",
	},
	{
		// (R) <sono><supiido><de>
		"\u305D\u306E\u30B9\u30D4\u30FC\u30C9\u3067",
		"d9juau41awczczp",
	},
	{
		// (S) -> $1.00 <-
		"\u002D\u003E\u0020\u0024\u0031\u002E\u0030\u0030\u0020" +
			"\u003C\u002D",
		"-> $1.00 <--",
	},
}
var domainAndTypeTests = [...]struct {
	host         string // host Set-Cookie header was received from
	domain       string // domain attribute in Set-Cookie header
	wantDomain   string // expected domain of cookie
	wantHostOnly bool   // expected host-cookie flag
	wantErr      error  // expected error
}{
	{"www.example.com", "", "www.example.com", true, nil},
	{"127.0.0.1", "", "127.0.0.1", true, nil},
	{"2001:4860:0:2001::68", "", "2001:4860:0:2001::68", true, nil},
	{"www.example.com", "example.com", "example.com", false, nil},
	{"www.example.com", ".example.com", "example.com", false, nil},
	{"www.example.com", "www.example.com", "www.example.com", false, nil},
	{"www.example.com", ".www.example.com", "www.example.com", false, nil},
	{"foo.sso.example.com", "sso.example.com", "sso.example.com", false, nil},
	{"bar.co.uk", "bar.co.uk", "bar.co.uk", false, nil},
	{"foo.bar.co.uk", ".bar.co.uk", "bar.co.uk", false, nil},
	{"127.0.0.1", "127.0.0.1", "127.0.0.1", true, nil},
	{"2001:4860:0:2001::68", "2001:4860:0:2001::68", "2001:4860:0:2001::68", true, nil},
	{"www.example.com", ".", "", false, errMalformedDomain},
	{"www.example.com", "..", "", false, errMalformedDomain},
	{"www.example.com", "other.com", "", false, errIllegalDomain},
	{"www.example.com", "com", "", false, errIllegalDomain},
	{"www.example.com", ".com", "", false, errIllegalDomain},
	{"foo.bar.co.uk", ".co.uk", "", false, errIllegalDomain},
	{"127.www.0.0.1", "127.0.0.1", "", false, errIllegalDomain},
	{"com", "", "com", true, nil},
	{"com", "com", "com", true, nil},
	{"com", ".com", "com", true, nil},
	{"co.uk", "", "co.uk", true, nil},
	{"co.uk", "co.uk", "co.uk", true, nil},
	{"co.uk", ".co.uk", "co.uk", true, nil},
}

// ───────────── 测试驱动 ─────────────

// tNow is the synthetic current time used as now during testing.
var tNow = time.Date(2013, 1, 1, 12, 0, 0, 0, time.UTC)

type testPSL struct{}

func (testPSL) String() string {
	return "testPSL"
}
func (testPSL) PublicSuffix(d string) string {
	if d == "co.uk" || strings.HasSuffix(d, ".co.uk") {
		return "co.uk"
	}
	if d == "www.buggy.psl" {
		return "xy"
	}
	if d == "www2.buggy.psl" {
		return "com"
	}
	return d[strings.LastIndex(d, ".")+1:]
}

func newTestJar() *Jar {
	jar, err := New(&Options{PublicSuffixList: testPSL{}})
	if err != nil {
		panic(err)
	}
	return jar
}

func expiresIn(delta int) string {
	t := tNow.Add(time.Duration(delta) * time.Second)
	return "expires=" + t.Format(time.RFC1123)
}

func mustParseURL(s string) *url.URL {
	u, err := url.Parse(s)
	if err != nil || u.Scheme == "" || u.Host == "" {
		panic(fmt.Sprintf("Unable to parse URL %s.", s))
	}
	return u
}

type jarTest struct {
	description string
	fromURL     string
	setCookies  []string
	content     string
	queries     []query
}

type query struct {
	toURL string
	want  string
}

func B(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

func (test jarTest) run(jar *Jar) {
	now := tNow
	fmt.Println("== " + test.description)
	var setCookies []*http.Cookie
	for _, cs := range test.setCookies {
		c, err := http.ParseSetCookie(cs)
		if err != nil {
			fmt.Println("  bad cookie line: " + cs)
			continue
		}
		setCookies = append(setCookies, c)
	}
	jar.setCookies(mustParseURL(test.fromURL), setCookies, now)
	now = now.Add(1001 * time.Millisecond)

	var cs []string
	for _, submap := range jar.entries {
		for _, cookie := range submap {
			if !cookie.Expires.After(now) {
				continue
			}
			v := cookie.Value
			if strings.ContainsAny(v, " ,") || cookie.Quoted {
				v = `"` + v + `"`
			}
			cs = append(cs, cookie.Name+"="+v)
		}
	}
	slices.Sort(cs)
	got := strings.Join(cs, " ")
	fmt.Println("  content: " + got + " ok=" + B(got == test.content))
	for _, query := range test.queries {
		now = now.Add(1001 * time.Millisecond)
		var s []string
		for _, c := range jar.cookies(mustParseURL(query.toURL), now) {
			s = append(s, c.String())
		}
		got := strings.Join(s, " ")
		fmt.Println("  " + query.toURL + " -> " + got + " ok=" + B(got == query.want))
	}
}

func ms(t time.Time) string {
	if t.IsZero() {
		return "zero"
	}
	return fmt.Sprint(t.UnixMilli())
}

func dumpEntries(jar *Jar) {
	var all []entry
	for _, submap := range jar.entries {
		for _, e := range submap {
			all = append(all, e)
		}
	}
	slices.SortFunc(all, func(a, b entry) int { return cmp.Compare(a.id(), b.id()) })
	for _, e := range all {
		fmt.Printf("  %s value=%s quoted=%s samesite=%s secure=%s httponly=%s persistent=%s hostonly=%s expires=%s creation=%s lastaccess=%s seq=%d\n",
			e.id(), e.Value, B(e.Quoted), e.SameSite, B(e.Secure), B(e.HttpOnly), B(e.Persistent), B(e.HostOnly),
			ms(e.Expires), ms(e.Creation), ms(e.LastAccess), e.seqNum)
	}
}

func parseLines(lines []string) []*http.Cookie {
	var out []*http.Cookie
	for _, l := range lines {
		c, err := http.ParseSetCookie(l)
		if err == nil {
			out = append(out, c)
		}
	}
	return out
}

func showCookies(cs []*http.Cookie) string {
	var s []string
	for _, c := range cs {
		s = append(s, c.String())
	}
	return "[" + strings.Join(s, " ") + "]"
}

func showParsed(c *http.Cookie) string {
	return fmt.Sprintf("name=[%s] value=[%s] quoted=%s path=[%s] domain=[%s] expires=%s raw=[%s] maxage=%d secure=%s httponly=%s samesite=%d partitioned=%s unparsed=[%s]",
		c.Name, c.Value, B(c.Quoted), c.Path, c.Domain, ms(c.Expires), c.RawExpires, c.MaxAge, B(c.Secure), B(c.HttpOnly),
		int(c.SameSite), B(c.Partitioned), strings.Join(c.Unparsed, "|"))
}

func errStr(err error) string {
	if err == nil {
		return "nil"
	}
	return err.Error()
}

var canonicalHostList = []string{
	"www.example.com", "WWW.EXAMPLE.COM", "wWw.eXAmple.CoM", "www.example.com:80", "192.168.0.10",
	"192.168.0.5:8080", "2001:4860:0:2001::68", "[2001:4860:0:::68]:8080", "www.bücher.de",
	"www.example.com.", ".", "..", "...", ".net", ".net.", "a..", "b.a..", "weird.stuff...",
	"[bad.unmatched.bracket:", "[::1]", "[::1]x:80", "a:b:c", "[a]:b]:1", "Bücher.DE", "日本語.jp:8080",
}

var hasPortList = []string{
	"www.example.com", "www.example.com:80", "127.0.0.1", "127.0.0.1:8080", "2001:4860:0:2001::68",
	"[2001::0:::68]:80", "[::1]", "a:b",
}

var jarKeyList = []string{
	"foo.www.example.com", "www.example.com", "example.com", "com", "foo.www.bbc.co.uk",
	"www.bbc.co.uk", "bbc.co.uk", "co.uk", "uk", "192.168.0.5", "www.buggy.psl", "www2.buggy.psl",
	"", ".", "..", ".net", "a.", "b.a.", "weird.stuff..", "::1",
}

var isIPList = []string{
	"127.0.0.1", "1.2.3.4", "2001:4860:0:2001::68", "::1%zone", "example.com", "1.1.1.300",
	"www.foo.bar.net", "123.foo.bar.net", "01.2.3.4", "::ffff:1.2.3.4", "", "1.2.3",
}

var defaultPathList = []string{
	"/", "/abc", "/abc/", "/abc/xyz", "/abc/xyz/", "/a/b/c.html", "", "strange", "//", "/a//b",
	"/a/./b", "/a/../b",
}

var parseSetCookieLines = []string{
	"", "   ", "noequal", "a b=1", "=v", "n=bad\\val", `n="x"`, `n="`, `n="a b"`, "ü=1",
	"n=v; Max-Age=0", "n=v; max-age=05", "n=v; max-age=-3", "n=v; max-age=abc", "n=v; max-age=+7",
	"n=v; max-age=3600",
	"n=v; Expires=Wed, 09 Jun 2021 10:18:14 GMT", "n=v; expires=Wed, 09-Jun-2021 10:18:14 GMT",
	"n=v; expires=Mon, 09 Jun 2021 10:18:14 UTC", "n=v; expires=bogus",
	"n=v; expires=Wed, 31 Feb 2021 10:18:14 GMT", "n=v; expires=Wed, 09 Jun 2021 24:18:14 GMT",
	"n=v; Partitioned; foo=bar; wéird=1", "n=v; path=/x; path=/y", `n=v; domain=a"b`,
	" n = v ; secure", "n=v;;  ; HTTPONLY", "n=v; SameSite=Lax", "n=v; samesite=STRICT",
	"n=v; samesite=None", "n=v; samesite", "n=v; samesite=bogus", "n=v; samesite=lâx",
	"n=v; Domain=.Example.COM; Path=/a/b",
}

func main() {
	fmt.Println("# hasDotSuffix")
	for _, tc := range hasDotSuffixTests {
		fmt.Printf("[%s] [%s] %s\n", tc.s, tc.suffix, B(hasDotSuffix(tc.s, tc.suffix)))
	}
	fmt.Println("# canonicalHost")
	for _, h := range canonicalHostList {
		got, err := canonicalHost(h)
		fmt.Printf("[%s] -> [%s] err=%s\n", h, got, errStr(err))
	}
	fmt.Println("# SplitHostPort")
	for _, h := range []string{"a:1", "a", "[::1]:80", "[::1]", "[::1]x", "[::1]:80:", "a:b:c", "a[:1", "a]:1", "[a]]:1", "[a:1", ":80", "[]:"} {
		host, port, err := net.SplitHostPort(h)
		fmt.Printf("[%s] -> [%s] [%s] err=%s\n", h, host, port, errStr(err))
	}
	fmt.Println("# hasPort")
	for _, h := range hasPortList {
		fmt.Printf("[%s] %s\n", h, B(hasPort(h)))
	}
	fmt.Println("# jarKey")
	for _, h := range jarKeyList {
		fmt.Printf("[%s] psl=[%s] nil=[%s]\n", h, jarKey(h, testPSL{}), jarKey(h, nil))
	}
	fmt.Println("# isIP")
	for _, h := range isIPList {
		fmt.Printf("[%s] %s\n", h, B(isIP(h)))
	}
	fmt.Println("# defaultPath")
	for _, p := range defaultPathList {
		fmt.Printf("[%s] -> [%s]\n", p, defaultPath(p))
	}
	fmt.Println("# domainAndType")
	{
		jar := newTestJar()
		for _, tc := range domainAndTypeTests {
			domain, hostOnly, err := jar.domainAndType(tc.host, tc.domain)
			fmt.Printf("[%s] [%s] -> [%s] %s err=%s\n", tc.host, tc.domain, domain, B(hostOnly), errStr(err))
		}
		jar2, _ := New(nil)
		for _, hd := range [][2]string{{"foo.bar.co.uk", ".co.uk"}, {"www.example.com", "com"}, {"x.com", "perché.com"}, {"x.com", "x.com."}, {"x.com", "X.COM"}} {
			domain, hostOnly, err := jar2.domainAndType(hd[0], hd[1])
			fmt.Printf("nil [%s] [%s] -> [%s] %s err=%s\n", hd[0], hd[1], domain, B(hostOnly), errStr(err))
		}
	}
	fmt.Println("# punycode")
	for _, tc := range punycodeTestCases {
		got, err := encode("", tc.s)
		fmt.Printf("[%s] ok=%s err=%s\n", got, B(got == tc.encoded), errStr(err))
	}
	for _, s := range []string{"golang", "bücher.example.com", "www.bücher.de", "日本語.jp", "a.ü.b", "", "Hello世界.com", "ÜBER.de"} {
		got, err := toASCII(s)
		fmt.Printf("toASCII [%s] -> [%s] err=%s\n", s, got, errStr(err))
	}

	fmt.Println("# basics")
	for _, test := range basicsTests {
		test.run(newTestJar())
	}
	fmt.Println("# updateAndDelete")
	{
		jar := newTestJar()
		for _, test := range updateAndDeleteTests {
			test.run(jar)
		}
	}
	fmt.Println("# expiration")
	jarTest{
		"Expiration.",
		"http://www.host.test",
		[]string{
			"a=1",
			"b=2; max-age=3",
			"c=3; " + expiresIn(3),
			"d=4; max-age=5",
			"e=5; " + expiresIn(5),
			"f=6; max-age=100",
		},
		"a=1 b=2 c=3 d=4 e=5 f=6",
		[]query{
			{"http://www.host.test", "a=1 b=2 c=3 d=4 e=5 f=6"},
			{"http://www.host.test", "a=1 d=4 e=5 f=6"},
			{"http://www.host.test", "a=1 d=4 e=5 f=6"},
			{"http://www.host.test", "a=1 f=6"},
			{"http://www.host.test", "a=1 f=6"},
		},
	}.run(newTestJar())
	fmt.Println("# chromiumBasics")
	for _, test := range chromiumBasicsTests {
		test.run(newTestJar())
	}
	fmt.Println("# chromiumDomain")
	{
		jar := newTestJar()
		for _, test := range chromiumDomainTests {
			test.run(jar)
		}
	}
	fmt.Println("# chromiumDeletion")
	{
		jar := newTestJar()
		for _, test := range chromiumDeletionTests {
			test.run(jar)
		}
	}
	fmt.Println("# domainHandling")
	for _, test := range domainHandlingTests {
		test.run(newTestJar())
	}

	fmt.Println("# issue19384")
	{
		cookies := []*http.Cookie{{Name: "name", Value: "value"}}
		for _, host := range []string{"", ".", "..", "..."} {
			jar, _ := New(nil)
			u := &url.URL{Scheme: "http", Host: host, Path: "/"}
			fmt.Printf("[%s] before=%s", host, showCookies(jar.Cookies(u)))
			jar.SetCookies(u, cookies)
			fmt.Printf(" after=%s\n", showCookies(jar.Cookies(u)))
		}
	}

	fmt.Println("# nil PSL")
	{
		jar, _ := New(nil)
		jar.setCookies(mustParseURL("http://www.bbc.co.uk/"), parseLines([]string{"a=1; domain=co.uk", "b=2"}), tNow)
		for _, q := range []string{"http://other.co.uk/", "http://www.bbc.co.uk/x", "http://co.uk/", "http://uk/"} {
			fmt.Println("  " + q + " -> " + showCookies(jar.cookies(mustParseURL(q), tNow)))
		}
	}

	fmt.Println("# entries")
	{
		jar := newTestJar()
		u := mustParseURL("http://www.host.test/dir/page")
		jar.setCookies(u, parseLines([]string{
			"a=1; SameSite=Lax; Secure",
			"b=2; samesite=strict; HttpOnly; max-age=60",
			"c=3; SameSite",
			"d=4; samesite=none",
			`e="q v"; Path=/p; Domain=.host.test; Expires=Tue, 01 Jan 2013 13:00:00 GMT`,
			"f=6; samesite=bogus",
			"x=1; max-age=2",
		}), tNow)
		dumpEntries(jar)
		fmt.Println("  query " + showCookies(jar.cookies(mustParseURL("https://www.host.test/dir/x"), tNow.Add(5*time.Second))))
		dumpEntries(jar)
		jar.setCookies(u, parseLines([]string{"a=changed", "x=again", "g=7; expires=Tue, 01 Jan 2013 11:00:00 GMT"}), tNow.Add(10*time.Second))
		dumpEntries(jar)
		fmt.Println("  query " + showCookies(jar.cookies(mustParseURL("https://www.host.test/dir/x"), tNow.Add(61*time.Second))))
		dumpEntries(jar)
		jar.setCookies(mustParseURL("ftp://www.host.test/"), parseLines([]string{"z=1"}), tNow)
		jar.setCookies(&url.URL{Scheme: "http", Host: "[bad", Path: "/"}, parseLines([]string{"z=1"}), tNow)
		jar.setCookies(u, nil, tNow)
		dumpEntries(jar)
	}

	fmt.Println("# ParseSetCookie")
	for _, l := range parseSetCookieLines {
		c, err := http.ParseSetCookie(l)
		if err != nil {
			fmt.Printf("[%s] err=%s\n", l, err)
			continue
		}
		fmt.Printf("[%s] %s\n", l, showParsed(c))
	}
	fmt.Println("# Cookie.String")
	exp := time.Date(2013, 1, 2, 3, 4, 5, 0, time.UTC)
	for _, c := range []*http.Cookie{
		{Name: "a", Value: "b"},
		{Name: "a", Value: "b c"},
		{Name: "a", Value: "b,c"},
		{Name: "a", Value: "x", Quoted: true},
		{Name: "a", Value: ""},
		{Name: "a b", Value: "x"},
		{Name: "", Value: "x"},
		{Name: "full", Value: "v", Path: "/p", Domain: ".example.com", Expires: exp, MaxAge: 10, HttpOnly: true, Secure: true, SameSite: http.SameSiteLaxMode, Partitioned: true},
		{Name: "neg", Value: "v", MaxAge: -1, SameSite: http.SameSiteNoneMode},
		{Name: "s", Value: "v", SameSite: http.SameSiteStrictMode, Domain: "1.2.3.4"},
		{Name: "d", Value: "v", SameSite: http.SameSiteDefaultMode, Domain: "example.com"},
		{Name: "old", Value: "v", Expires: time.Date(1600, 12, 31, 0, 0, 0, 0, time.UTC)},
		{Name: "y1601", Value: "v", Expires: time.Date(1601, 1, 1, 0, 0, 0, 0, time.UTC)},
	} {
		fmt.Printf("[%s]\n", c.String())
	}
	_ = errors.New
	_ = netip.ParseAddr
	_ = utf8.RuneSelf
}
