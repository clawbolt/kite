//go:build ignore

// tests/go/hpke.go：对拍 Go 1.27 crypto/hpke。它要用包内的 setupDerandomizedEncap（确定的封装），所以是 crypto/hpke 的内部测试：
//   GO=/path/to/go sh tests/go/hpke_regen.sh
// 把本文件拷成 $GOROOT/src/crypto/hpke/zz_kite_gen_test.go 跑 TestKiteGen。HPKE_N 是累积加密 / 导出的次数
// （Go 自己的测试和测试向量用 1000；expected 用 HPKE_N=20 生成，"want" 只在 1000 次时真正比较）。
package hpke

import (
	"crypto/ecdh"
	"crypto/sha3"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"testing"

	"golang.org/x/crypto/chacha20poly1305"
)

type kiteVec struct {
	Mode        uint16 `json:"mode"`
	KEM         uint16 `json:"kem_id"`
	KDF         uint16 `json:"kdf_id"`
	AEAD        uint16 `json:"aead_id"`
	Info        string `json:"info"`
	IkmE        string `json:"ikmE"`
	IkmR        string `json:"ikmR"`
	SkRm        string `json:"skRm"`
	PkRm        string `json:"pkRm"`
	Enc         string `json:"enc"`
	Encryptions []struct {
		Aad string `json:"aad"`
		Ct  string `json:"ct"`
		Pt  string `json:"pt"`
	} `json:"encryptions"`
	Exports []struct {
		Context string `json:"exporter_context"`
		L       int    `json:"L"`
		Value   string `json:"exported_value"`
	} `json:"exports"`
	AccEncryptions string `json:"encryptions_accumulated"`
	AccExports     string `json:"exports_accumulated"`
}

func kh(b []byte) string { return hex.EncodeToString(b) }

func kun(s string) []byte { b, _ := hex.DecodeString(s); return b }

func kres(b []byte, err error) string {
	if err != nil {
		return "err " + err.Error()
	}
	return "ok " + kh(b)
}

func kerr(err error) string { return kres(nil, err) }

func kdraw(r *sha3.SHAKE) []byte {
	l := make([]byte, 1)
	r.Read(l)
	b := make([]byte, int(l[0]))
	r.Read(b)
	return b
}

func kiteVector(t *testing.T, file string, v kiteVec, n int) {
	fmt.Printf("vector %s mode %04x kem %04x kdf %04x aead %04x\n", file, v.Mode, v.KEM, v.KDF, v.AEAD)
	if v.Mode != 0 || v.KEM == 0x21 || v.KEM == 0x40 || v.KDF == 0x12 || v.KDF == 0x13 {
		fmt.Println("  skip")
		return
	}
	kdf, _ := NewKDF(v.KDF)
	aead, _ := NewAEAD(v.AEAD)
	kem, _ := NewKEM(v.KEM)
	pkb := kun(v.PkRm)
	pk, err := kem.NewPublicKey(pkb)
	if err != nil {
		t.Fatal(err)
	}
	fmt.Printf("  pk-eq %v kem-eq %v\n", string(pk.Bytes()) == string(pkb), pk.KEM() == kem)
	info := kun(v.Info)
	t.Run("x", func(t *testing.T) {
		setupDerandomizedEncap(t, kun(v.IkmE), pk)
		enc, s, err := NewSender(pk, kdf, aead, info)
		if err != nil {
			t.Fatal(err)
		}
		fmt.Printf("  enc %s size %v want %v\n", kh(enc), len(enc) == kem.encSize(), kh(enc) == v.Enc)
		skb := kun(v.SkRm)
		sk, _ := kem.NewPrivateKey(skb)
		skb2, _ := sk.Bytes()
		fmt.Printf("  sk %s eq %v pub-eq %v\n", kh(skb2), kh(skb2) == v.SkRm, kh(sk.PublicKey().Bytes()) == v.PkRm)
		d, _ := kem.DeriveKeyPair(kun(v.IkmR))
		db, _ := d.Bytes()
		fmt.Printf("  derived %s pub-eq %v\n", kh(db), kh(d.PublicKey().Bytes()) == v.PkRm)
		r, err := NewRecipient(enc, sk, kdf, aead, info)
		if err != nil {
			t.Fatal(err)
		}
		if aead != ExportOnly() && v.AccEncryptions != "" {
			src, sink := sha3.NewSHAKE128(), sha3.NewSHAKE128()
			ok := true
			for range n {
				aad, pt := kdraw(src), kdraw(src)
				c, _ := s.Seal(aad, pt)
				sink.Write(c)
				p, _ := r.Open(aad, c)
				if string(p) != string(pt) {
					ok = false
				}
			}
			acc := make([]byte, 16)
			sink.Read(acc)
			fmt.Printf("  acc-enc %s open %v want %v\n", kh(acc), ok, n != 1000 || kh(acc) == v.AccEncryptions)
		} else if aead != ExportOnly() {
			for _, e := range v.Encryptions {
				aad, pt := kun(e.Aad), kun(e.Pt)
				c, _ := s.Seal(aad, pt)
				p, _ := r.Open(aad, c)
				fmt.Printf("  ct %s want %v open %v\n", kh(c), kh(c) == e.Ct, string(p) == string(pt))
			}
		} else {
			_, e1 := s.Seal(nil, nil)
			_, e2 := r.Open(nil, nil)
			fmt.Printf("  export-only %s / %s\n", kerr(e1), kerr(e2))
		}
		if v.AccExports != "" {
			src, sink := sha3.NewSHAKE128(), sha3.NewSHAKE128()
			ok := true
			for l := range n {
				c := string(kdraw(src))
				val, _ := s.Export(c, l)
				sink.Write(val)
				got, _ := r.Export(c, l)
				if string(got) != string(val) {
					ok = false
				}
			}
			acc := make([]byte, 16)
			sink.Read(acc)
			fmt.Printf("  acc-exp %s recv %v want %v\n", kh(acc), ok, n != 1000 || kh(acc) == v.AccExports)
		} else {
			for _, e := range v.Exports {
				c := string(kun(e.Context))
				val, _ := s.Export(c, e.L)
				got, _ := r.Export(c, e.L)
				fmt.Printf("  export %s want %v recv %v\n", kh(val), kh(val) == e.Value, string(got) == string(val))
			}
		}
	})
}

func kder(kem KEM, ikm []byte) []byte {
	switch kem {
	case MLKEM768(), MLKEM1024():
		return ikm[:32]
	case MLKEM1024P384():
		return ikm[:80]
	case MLKEM768X25519(), MLKEM768P256():
		return ikm[:64]
	}
	return ikm
}

func kiteErrors(t *testing.T) {
	_, err := NewKEM(0x21)
	fmt.Println("new-kem " + kerr(err))
	_, err = NewKDF(0x12)
	fmt.Println("new-kdf " + kerr(err))
	_, err = NewAEAD(4)
	fmt.Println("new-aead " + kerr(err))
	ikm := make([]byte, 80)
	for i := range ikm {
		ikm[i] = byte(i)
	}
	kems := []KEM{DHKEM(ecdh.P256()), DHKEM(ecdh.P384()), DHKEM(ecdh.P521()), DHKEM(ecdh.X25519()), MLKEM768(), MLKEM1024(),
		MLKEM768P256(), MLKEM1024P384(), MLKEM768X25519()}
	for _, kem := range kems {
		t.Run("x", func(t *testing.T) {
			sk, _ := kem.DeriveKeyPair(ikm)
			pk := sk.PublicKey()
			kdf, aead := HKDFSHA256(), AES128GCM()
			setupDerandomizedEncap(t, kder(kem, ikm), pk)
			enc, s0, err := NewSender(pk, kdf, aead, []byte("info"))
			if err != nil {
				t.Fatal(err)
			}
			ct, _ := s0.Seal([]byte("aad"), []byte("plaintext"))
			c := append(append([]byte(nil), enc...), ct...)
			ph := sha3.Sum256(pk.Bytes())
			ch := sha3.Sum256(c)
			fmt.Printf("kem %04x pk %s ct %s\n", kem.ID(), kh(ph[:]), kh(ch[:]))
			fmt.Println("  open " + kres(Open(sk, kdf, aead, []byte("info"), c)))
			fmt.Println("  wrong-info " + kres(Open(sk, kdf, aead, []byte("wrong"), c)))
			bad := append([]byte(nil), c...)
			bad[len(bad)-1] ^= 0xFF
			fmt.Println("  corrupted " + kres(Open(sk, kdf, aead, []byte("info"), bad)))
			fmt.Println("  short " + kres(Open(sk, kdf, aead, []byte("info"), c[:kem.encSize()-1])))
			_, err = NewRecipient(enc[:len(enc)-1], sk, kdf, aead, nil)
			fmt.Println("  enc-short " + kerr(err))
			_, err = kem.NewPublicKey(pk.Bytes()[1:])
			fmt.Println("  pk-short " + kerr(err))
			kb, _ := sk.Bytes()
			_, err = kem.NewPrivateKey(kb[1:])
			fmt.Println("  sk-short " + kerr(err))
			r, _ := NewRecipient(enc, sk, kdf, aead, []byte("info"))
			a, e1 := r.Export("", -1)
			b, e2 := r.Export("", 65536)
			cc, e3 := r.Export("", 8161)
			fmt.Println("  export-neg " + kres(a, e1) + " / big " + kres(b, e2) + " / huge " + kres(cc, e3))
			enc2, s2, err := NewSender(pk, SHAKE256(), ExportOnly(), nil)
			if err != nil {
				t.Fatal(err)
			}
			_, se := s2.Seal(nil, nil)
			fmt.Println("  export-only " + kerr(se) + " / " + kres(s2.Export("ctx", 40)))
			r2, _ := NewRecipient(enc2, sk, SHAKE256(), ExportOnly(), nil)
			fmt.Println("  export-recv " + kres(r2.Export("ctx", 40)))
			kk, _ := kem.NewPrivateKey(kb)
			kb2, _ := kk.Bytes()
			c2, _ := Seal(pk, HKDFSHA512(), ChaCha20Poly1305(), nil, nil)
			fmt.Printf("  roundtrip %v %s\n", string(kb2) == string(kb), kres(Open(kk, HKDFSHA512(), ChaCha20Poly1305(), nil, c2)))
		})
	}
	xs, _ := DHKEM(ecdh.X25519()).DeriveKeyPair([]byte{1, 2, 3})
	_, err = NewRecipient(make([]byte, 32), xs, HKDFSHA256(), AES128GCM(), nil)
	fmt.Println("x25519-low-order " + kerr(err))
	ps, _ := DHKEM(ecdh.P256()).DeriveKeyPair([]byte{1, 2, 3})
	bp := []byte{4}
	for range 64 {
		bp = append(bp, 1)
	}
	_, err = NewRecipient(bp, ps, HKDFSHA256(), AES128GCM(), nil)
	fmt.Println("p256-bad-point " + kerr(err))
	_, err = MLKEM768X25519().NewPrivateKey([]byte{1, 2, 3})
	fmt.Println("hybrid-seed " + kerr(err))
	_, err = DHKEM(ecdh.P256()).NewPrivateKey(make([]byte, 32))
	fmt.Println("p256-zero-key " + kerr(err))
}

func kiteChaCha() {
	key := make([]byte, 32)
	for i := range key {
		key[i] = byte(0x80 + i)
	}
	nonce := []byte{7, 0, 0, 0, 0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47}
	aad := []byte{0x50, 0x51, 0x52, 0x53, 0xc0, 0xc1, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7}
	pt := []byte("Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it.")
	a, _ := chacha20poly1305.New(key)
	ct := a.Seal(nil, nonce, pt, aad)
	fmt.Println("chacha rfc8439 " + kh(ct))
	fmt.Println("chacha open " + kres(a.Open(nil, nonce, ct, aad)))
	bad := append([]byte(nil), ct...)
	bad[0] ^= 1
	fmt.Println("chacha bad " + kres(a.Open(nil, nonce, bad, aad)))
	fmt.Println("chacha short " + kres(a.Open(nil, nonce, ct[:15], aad)))
	_, err := chacha20poly1305.New(key[:31])
	fmt.Println("chacha key " + kerr(err))
	x, _ := chacha20poly1305.NewX(key)
	xn := make([]byte, 24)
	for i := range xn {
		xn[i] = byte(0x40 + i)
	}
	for _, l := range []int{0, 1, 15, 16, 17, 63, 64, 65, 200} {
		m := make([]byte, l)
		for i := range m {
			m[i] = byte(i * 7)
		}
		c1 := a.Seal(nil, nonce, m, nil)
		c2 := x.Seal(nil, xn, m, m[:l/2])
		p, _ := x.Open(nil, xn, c2, m[:l/2])
		h1 := sha3.Sum256(c1)
		h2 := sha3.Sum256(c2)
		fmt.Printf("chacha %d %s x %s %v\n", l, kh(h1[:]), kh(h2[:]), string(p) == string(m))
	}
}

func TestKiteGen(t *testing.T) {
	n := 1000
	if s := os.Getenv("HPKE_N"); s != "" {
		n, _ = strconv.Atoi(s)
	}
	for _, file := range []string{"rfc9180", "hpke-pq"} {
		data, err := os.ReadFile("testdata/" + file + ".json")
		if err != nil {
			t.Fatal(err)
		}
		var vs []kiteVec
		if err := json.Unmarshal(data, &vs); err != nil {
			t.Fatal(err)
		}
		for _, v := range vs {
			kiteVector(t, file, v, n)
		}
	}
	kiteErrors(t)
	kiteChaCha()
}
