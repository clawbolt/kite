package main

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

var paths = []string{"abc", "abc/def", "a/b/c", ".", "..", "../..", "../../abc", "/abc", "/", "", "abc/", "abc/def/", "a/b/c/", "./", "../", "../../", "/abc/", "abc//def//ghi", "abc//", "abc/./def", "/./abc/def", "abc/.", "abc/def/ghi/../jkl", "abc/def/../ghi/../jkl", "abc/def/..", "abc/def/../..", "/abc/def/../..", "abc/def/../../..", "/abc/def/../../..", "abc/def/../../../ghi/jkl/../../../mno", "/../abc", "a/../b:/../../c", "abc/./../def", "abc//./../def", "abc/../../././../def", "../a", "/a/../..", "a", "a/../a", "a/", "a/./b/./c", "path.go", "path.pb.go", "a.dir/b", "a.dir/b.go", "a.dir/", "/.", "////", "x/", "a/b/.x", "a/b/c.", "a/b/c.x", "/foo", "/usr/bin/gcc", "/a/../bb", "lala", "é/ü.txt", "dir/文件.tar.gz", "a//b", "x/../../y", ".hidden", "a/.b", ".../x", "a\\b", "C:\\x", "~/x", "a b/c d.txt", "//a//b//"}
var joins = [][]string{[]string{}, []string{""}, []string{"/"}, []string{"a"}, []string{"a", "b"}, []string{"a", ""}, []string{"", "b"}, []string{"/", "a"}, []string{"/", "a/b"}, []string{"/", ""}, []string{"/a", "b"}, []string{"a", "/b"}, []string{"/a", "/b"}, []string{"a/", "b"}, []string{"a/", ""}, []string{"", ""}, []string{"/", "a", "b"}, []string{"//", "a"}, []string{"", "", "x", "", "y"}, []string{"a", "..", "..", "b"}, []string{"a/b", "../../../c"}, []string{"..", "x"}}
var rels = [][2]string{{"a/b", "a/b"}, {"a/b/.", "a/b"}, {"a/b", "a/b/."}, {"./a/b", "a/b"}, {"a/b", "./a/b"}, {"ab/cd", "ab/cde"}, {"ab/cd", "ab/c"}, {"a/b", "a/b/c/d"}, {"a/b", "a/b/../c"}, {"a/b/../c", "a/b"}, {"a/b/c", "a/c/d"}, {"a/b", "c/d"}, {"a/b/c/d", "a/b"}, {"a/b/c/d", "a/b/"}, {"a/b/c/d/", "a/b"}, {"a/b/c/d/", "a/b/"}, {"../../a/b", "../../a/b/c/d"}, {"/a/b", "/a/b"}, {"/a/b/.", "/a/b"}, {"/a/b", "/a/b/."}, {"/ab/cd", "/ab/cde"}, {"/ab/cd", "/ab/c"}, {"/a/b", "/a/b/c/d"}, {"/a/b", "/a/b/../c"}, {"/a/b/../c", "/a/b"}, {"/a/b/c", "/a/c/d"}, {"/a/b", "/c/d"}, {"/a/b/c/d", "/a/b"}, {"/a/b/c/d", "/a/b/"}, {"/a/b/c/d/", "/a/b"}, {"/a/b/c/d/", "/a/b/"}, {"/../../a/b", "/../../a/b/c/d"}, {".", "a/b"}, {".", ".."}, {"", "../../."}, {"..", "."}, {"..", "a"}, {"../..", ".."}, {"a", "/a"}, {"/a", "a"}, {"/", "/x/y"}, {"/x/y", "/"}, {"x", "x/../y"}, {"a/..b", "a/c"}}
var ms = [][2]string{{"abc", "abc"}, {"*", "abc"}, {"*c", "abc"}, {"a*", "a"}, {"a*", "abc"}, {"a*", "ab/c"}, {"a*/b", "abc/b"}, {"a*/b", "a/c/b"}, {"a*b*c*d*e*/f", "axbxcxdxe/f"}, {"a*b*c*d*e*/f", "axbxcxdxexxx/f"}, {"a*b*c*d*e*/f", "axbxcxdxe/xxx/f"}, {"a*b*c*d*e*/f", "axbxcxdxexxx/fff"}, {"a*b?c*x", "abxbbxdbxebxczzx"}, {"a*b?c*x", "abxbbxdbxebxczzy"}, {"ab[c]", "abc"}, {"ab[b-d]", "abc"}, {"ab[e-g]", "abc"}, {"ab[^c]", "abc"}, {"ab[^b-d]", "abc"}, {"ab[^e-g]", "abc"}, {"a\\*b", "a*b"}, {"a\\*b", "ab"}, {"a?b", "a☺b"}, {"a[^a]b", "a☺b"}, {"a???b", "a☺b"}, {"a[^a][^a][^a]b", "a☺b"}, {"[a-ζ]*", "α"}, {"*[a-ζ]", "A"}, {"a?b", "a/b"}, {"a*b", "a/b"}, {"[\\]a]", "]"}, {"[\\-]", "-"}, {"[x\\-]", "x"}, {"[x\\-]", "-"}, {"[x\\-]", "z"}, {"[\\-x]", "x"}, {"[\\-x]", "-"}, {"[\\-x]", "a"}, {"[]a]", "]"}, {"[-]", "-"}, {"[x-]", "x"}, {"[x-]", "-"}, {"[x-]", "z"}, {"[-x]", "x"}, {"[-x]", "-"}, {"[-x]", "a"}, {"\\", "a"}, {"[a-b-c]", "a"}, {"[", "a"}, {"[^", "a"}, {"[^bc", "a"}, {"a[", "a"}, {"a[", "ab"}, {"a[", "x"}, {"a/b[", "x"}, {"*x", "xxx"}, {"*.go", "main.go"}, {"*.go", "dir/main.go"}, {"*/*.go", "dir/main.go"}, {"a*", "ab["}, {"[a-c]*[0-9]", "b12"}, {"**", "a/b"}, {"*/", "a/"}, {"", ""}, {"", "a"}, {"?", "é"}, {"[é-ü]", "ö"}, {"x*y*", "x/y"}}
var lists = []string{"", "a:b", ":a:b", "a::b:", "/usr/bin:/bin", ":"}

func q(s string) string { return "\"" + s + "\"" }

func sl(xs []string) string {
	ps := []string{}
	for _, x := range xs {
		ps = append(ps, q(x))
	}
	return "[" + strings.Join(ps, ", ") + "]"
}

func rs(s string, err error) string {
	if err != nil {
		return "err:" + err.Error()
	}
	return "ok:" + q(s)
}

func es(err error) string {
	if err != nil {
		return err.Error()
	}
	return "nil"
}

const root = "/tmp/kite_filepath_test"

func setup() {
	os.RemoveAll(root)
	for _, d := range []string{"dir1/sub", "dir2", "skip", "empty"} {
		os.MkdirAll(root+"/"+d, 0755)
	}
	files := [][2]string{{"a.txt", "hello"}, {"b.go", "package b"}, {".hidden", "h"}, {"dir1/c.txt", "c"},
		{"dir1/d.md", "dd"}, {"dir1/sub/e.txt", "eee"}, {"dir2/f.txt", "ffff"}, {"skip/g.txt", "g"}, {"空 格.txt", "u"}}
	for _, f := range files {
		os.WriteFile(root+"/"+f[0], []byte(f[1]), 0644)
	}
	links := [][2]string{{"dir1", "link_dir"}, {"dir1/sub/e.txt", "link_rel"}, {root + "/dir2", "link_abs"},
		{"nonexist", "broken"}, {"loop2", "loop1"}, {"loop1", "loop2"}, {"../dir1", "dir2/up"}, {"link_dir", "chain"}}
	for _, l := range links {
		os.Symlink(l[0], root+"/"+l[1])
	}
}

func entry(d fs.DirEntry) string {
	if d == nil {
		return "nil"
	}
	return fmt.Sprintf("%s dir=%v link=%v", d.Name(), d.IsDir(), d.Type()&fs.ModeSymlink != 0)
}

func finfo(fi fs.FileInfo) string {
	if fi == nil {
		return "nil"
	}
	s := fmt.Sprintf("%s dir=%v link=%v", fi.Name(), fi.IsDir(), fi.Mode()&fs.ModeSymlink != 0)
	if fi.Mode().IsRegular() {
		s += fmt.Sprintf(" size=%d perm=%o", fi.Size(), fi.Mode().Perm())
	}
	return s
}

func main() {
	fmt.Println("sep", string(filepath.Separator), string(filepath.ListSeparator))
	fmt.Println(filepath.ErrBadPattern, "|", filepath.SkipDir, "|", filepath.SkipAll)
	for _, p := range paths {
		d, f := filepath.Split(p)
		loc, err := filepath.Localize(p)
		fmt.Printf("%s clean=%s split=%s,%s ext=%s base=%s dir=%s abs=%v local=%v vol=%s slash=%s,%s localize=%s\n",
			q(p), q(filepath.Clean(p)), q(d), q(f), q(filepath.Ext(p)), q(filepath.Base(p)), q(filepath.Dir(p)),
			filepath.IsAbs(p), filepath.IsLocal(p), q(filepath.VolumeName(p)), q(filepath.ToSlash(p)),
			q(filepath.FromSlash(p)), rs(loc, err))
	}
	loc, err := filepath.Localize("a\x00b")
	fmt.Println("localize nul", rs(loc, err))
	for _, j := range joins {
		fmt.Println("join", sl(j), q(filepath.Join(j...)))
	}
	for _, r := range rels {
		s, err := filepath.Rel(r[0], r[1])
		fmt.Println("rel", q(r[0]), q(r[1]), rs(s, err))
	}
	for _, m := range ms {
		ok, err := filepath.Match(m[0], m[1])
		fmt.Println("match", q(m[0]), q(m[1]), ok, es(err))
	}
	for _, l := range lists {
		fmt.Println("splitlist", q(l), sl(filepath.SplitList(l)))
	}
	fmt.Println("hasprefix", filepath.HasPrefix("/a/b", "/a"), filepath.HasPrefix("ab", "b"))

	setup()
	os.Chdir(root)
	for _, p := range []string{"x", "dir1/../y", "/a/./b", "", ".", "../z"} {
		s, err := filepath.Abs(p)
		fmt.Println("abs", q(p), rs(s, err))
	}
	globs := []string{"*", "*.txt", "dir*/*.txt", "*/*", "*/sub/*", root + "/d*", "dir1/[cd].*", "nomatch*", "a.txt",
		"missing", "[", "dir1/\\c.txt", "dir[/x", ".*", "link_dir/*", "*/[", "[*/x", "*/*/*.txt", "空*", "b.go/*", "dir?"}
	for _, g := range globs {
		m, err := filepath.Glob(g)
		fmt.Println("glob", q(g), sl(m), es(err))
	}
	evals := []string{"link_dir", "link_dir/sub/e.txt", "link_rel", "link_abs/f.txt", "broken", "loop1", "a.txt/x",
		"dir2/up/c.txt", "chain/../a.txt", root + "/link_dir", "./dir1//sub/", ".", "..", "../kite_filepath_test/chain",
		"missing/x", "", "dir2/up/..", root + "/dir2/up/sub/../d.md"}
	for _, e := range evals {
		s, err := filepath.EvalSymlinks(e)
		fmt.Println("eval", q(e), rs(s, err))
	}

	fmt.Println("walkdir all", es(filepath.WalkDir(".", func(p string, d fs.DirEntry, err error) error {
		fmt.Println(" wd", q(p), entry(d), es(err))
		return nil
	})))
	fmt.Println("walkdir skip", es(filepath.WalkDir(".", func(p string, d fs.DirEntry, err error) error {
		fmt.Println(" wd", q(p))
		if p == "skip" || p == "dir1/c.txt" {
			return filepath.SkipDir
		}
		if p == "link_abs" {
			return filepath.SkipAll
		}
		return nil
	})))
	fmt.Println("walkdir err", es(filepath.WalkDir("dir1", func(p string, d fs.DirEntry, err error) error {
		fmt.Println(" wd", q(p))
		if p == "dir1/d.md" {
			return errors.New("boom at " + p)
		}
		return nil
	})))
	fmt.Println("walkdir missing", es(filepath.WalkDir("missing", func(p string, d fs.DirEntry, err error) error {
		fmt.Println(" wd", q(p), entry(d), es(err))
		return err
	})))
	fmt.Println("walkdir missing2", es(filepath.WalkDir("missing", func(p string, d fs.DirEntry, err error) error {
		return nil
	})))
	fmt.Println("walkdir rootskip", es(filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		fmt.Println(" wd", q(p))
		return filepath.SkipDir
	})))
	fmt.Println("walkdir file", es(filepath.WalkDir("link_dir", func(p string, d fs.DirEntry, err error) error {
		fmt.Println(" wd", q(p), entry(d), es(err))
		return nil
	})))

	fmt.Println("walk all", es(filepath.Walk(".", func(p string, fi fs.FileInfo, err error) error {
		fmt.Println(" w", q(p), finfo(fi), es(err))
		return nil
	})))
	fmt.Println("walk skip", es(filepath.Walk(".", func(p string, fi fs.FileInfo, err error) error {
		fmt.Println(" w", q(p))
		if p == "dir1" || p == "dir2/f.txt" {
			return filepath.SkipDir
		}
		if p == "skip/g.txt" {
			return filepath.SkipAll
		}
		return nil
	})))
	fmt.Println("walk file", es(filepath.Walk("a.txt", func(p string, fi fs.FileInfo, err error) error {
		fmt.Println(" w", q(p), finfo(fi), es(err))
		return nil
	})))
	fmt.Println("walk missing", es(filepath.Walk(root+"/missing", func(p string, fi fs.FileInfo, err error) error {
		fmt.Println(" w", q(p), finfo(fi), es(err))
		return err
	})))
	fmt.Println("walk err", es(filepath.Walk(root+"/dir1", func(p string, fi fs.FileInfo, err error) error {
		fmt.Println(" w", q(p))
		if strings.HasSuffix(p, "sub") {
			return errors.New("stop " + filepath.Base(p))
		}
		return nil
	})))
	os.Chdir("/")
	os.RemoveAll(root)
}
