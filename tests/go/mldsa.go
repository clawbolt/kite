package main

import (
	"bytes"
	"crypto"
	"crypto/mldsa"
	"crypto/sha256"
	"crypto/sha3"
	"encoding/hex"
	"fmt"
	"strings"
)

func h(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:12]) }

func es(err error) string {
	if err == nil {
		return "<nil>"
	}
	return err.Error()
}

func main() {
	fmt.Println("SIZES", mldsa.PrivateKeySize, mldsa.MLDSA44PublicKeySize, mldsa.MLDSA65PublicKeySize,
		mldsa.MLDSA87PublicKeySize, mldsa.MLDSA44SignatureSize, mldsa.MLDSA65SignatureSize, mldsa.MLDSA87SignatureSize)
	fmt.Println("HASHFUNC", int((&mldsa.Options{}).HashFunc()))
	params := []mldsa.Parameters{mldsa.MLDSA44(), mldsa.MLDSA65(), mldsa.MLDSA87()}
	for pi, p := range params {
		fmt.Println(p.String(), p.PublicKeySize(), p.SignatureSize())
		seed := make([]byte, 32)
		for j := range seed {
			seed[j] = byte(j*11 + pi*29 + 1)
		}
		sk, err := mldsa.NewPrivateKey(p, seed)
		pk := sk.PublicKey()
		fmt.Println(" KEY", es(err), h(pk.Bytes()), len(pk.Bytes()), hex.EncodeToString(pk.Bytes()[:8]),
			hex.EncodeToString(sk.Bytes()[:8]), pk.Parameters().String(), pk.Parameters() == p)
		msg := []byte("hello, ML-DSA " + p.String())
		sig, err := sk.SignDeterministic(msg, nil)
		fmt.Println(" SIG", es(err), h(sig), len(sig), hex.EncodeToString(sig[:8]))
		fmt.Println(" V", es(mldsa.Verify(pk, msg, sig, nil)), es(mldsa.Verify(pk, msg, sig, &mldsa.Options{})))
		fmt.Println(" VMSG", es(mldsa.Verify(pk, msg[1:], sig, nil)))
		fmt.Println(" VCTX", es(mldsa.Verify(pk, msg, sig, &mldsa.Options{Context: "x"})))
		opts := &mldsa.Options{Context: "kite context"}
		sig2, err := sk.SignDeterministic(msg, opts)
		fmt.Println(" SIGCTX", es(err), h(sig2), len(sig2))
		fmt.Println(" VCTX2", es(mldsa.Verify(pk, msg, sig2, opts)), es(mldsa.Verify(pk, msg, sig2, nil)))
		sig3, err := sk.SignDeterministic(nil, nil)
		fmt.Println(" SIGEMPTY", es(err), h(sig3), es(mldsa.Verify(pk, nil, sig3, nil)))
		// 篡改
		for _, idx := range []int{0, 40, len(sig) - p.SignatureSize()/3, len(sig) - 1} {
			for _, v := range []byte{1, 0x80} {
				bad := bytes.Clone(sig)
				bad[idx] ^= v
				fmt.Println(" TAMPER", idx, v, es(mldsa.Verify(pk, msg, bad, nil)))
			}
		}
		bad := bytes.Clone(sig)
		bad[len(bad)-1] = 0xff
		fmt.Println(" HINT1", es(mldsa.Verify(pk, msg, bad, nil)))
		bad = bytes.Clone(sig)
		bad[len(bad)-2] = 0
		fmt.Println(" HINT2", es(mldsa.Verify(pk, msg, bad, nil)))
		bad = bytes.Clone(sig)
		bad[len(bad)-p.SignatureSize()+p.SignatureSize()-12] ^= 0x5a
		fmt.Println(" HINT3", es(mldsa.Verify(pk, msg, bad, nil)))
		fmt.Println(" SHORT", es(mldsa.Verify(pk, msg, sig[1:], nil)), es(mldsa.Verify(pk, msg, append(bytes.Clone(sig), 0), nil)))
		long := strings.Repeat("a", 256)
		_, err = sk.SignDeterministic(msg, &mldsa.Options{Context: long})
		fmt.Println(" CTXLONG", es(err), es(mldsa.Verify(pk, msg, sig, &mldsa.Options{Context: long})))
		_, err = sk.SignDeterministic(msg, &mldsa.Options{Context: long[:255]})
		fmt.Println(" CTX255", es(err))
		// 公钥编解码
		pk2, err := mldsa.NewPublicKey(p, pk.Bytes())
		fmt.Println(" PK", es(err), pk2.Equal(pk), h(pk2.Bytes()), es(mldsa.Verify(pk2, msg, sig, nil)))
		_, err = mldsa.NewPublicKey(p, pk.Bytes()[1:])
		fmt.Println(" PKLEN", es(err))
		other := params[(pi+1)%3]
		_, err = mldsa.NewPublicKey(other, pk.Bytes())
		fmt.Println(" PKOTHER", es(err))
		bpk := bytes.Clone(pk.Bytes())
		bpk[100] ^= 4
		pk3, _ := mldsa.NewPublicKey(p, bpk)
		fmt.Println(" PKBAD", pk3.Equal(pk), es(mldsa.Verify(pk3, msg, sig, nil)))
		// 私钥
		sk2, err := mldsa.NewPrivateKey(p, sk.Bytes())
		fmt.Println(" SK", es(err), sk2.Equal(sk), sk2.PublicKey().Equal(pk))
		seed2 := bytes.Clone(seed)
		seed2[0] ^= 1
		sk3, _ := mldsa.NewPrivateKey(p, seed2)
		fmt.Println(" SK2", sk3.Equal(sk), sk3.PublicKey().Equal(pk), h(sk3.PublicKey().Bytes()),
			es(mldsa.Verify(sk3.PublicKey(), msg, sig, nil)))
		sko, _ := mldsa.NewPrivateKey(other, seed)
		fmt.Println(" SKOTHER", sko.Equal(sk), h(sko.PublicKey().Bytes()))
		_, err = mldsa.NewPrivateKey(p, seed[:31])
		fmt.Println(" SEEDLEN", es(err))
		// 外部 μ
		H := sha3.NewSHAKE256()
		H.Write(pk.Bytes())
		tr := make([]byte, 64)
		H.Read(tr)
		H = sha3.NewSHAKE256()
		H.Write(tr)
		H.Write([]byte{0, 0})
		H.Write(msg)
		mu := make([]byte, 64)
		H.Read(mu)
		sigmu, err := sk.SignDeterministic(mu, crypto.MLDSAMu)
		fmt.Println(" MU", es(err), bytes.Equal(sigmu, sig))
		_, err = sk.SignDeterministic(mu[:63], crypto.MLDSAMu)
		fmt.Println(" MULEN", es(err))
		_, err = sk.Sign(nil, mu[:63], crypto.MLDSAMu)
		fmt.Println(" MULEN2", es(err))
		_, err = sk.SignDeterministic(msg, crypto.SHA256)
		fmt.Println(" BADOPTS", es(err))
		_, err = sk.Sign(nil, msg, crypto.SHA256)
		fmt.Println(" BADOPTS2", es(err))
		s0, err := sk.SignDeterministic(msg, crypto.Hash(0))
		fmt.Println(" HASH0", es(err), bytes.Equal(s0, sig))
		// hedged（随机）签名：只检查能验签、长度正确、和确定性签名不同
		rs, err := sk.Sign(nil, msg, opts)
		fmt.Println(" HEDGED", es(err), len(rs), es(mldsa.Verify(pk, msg, rs, opts)), bytes.Equal(rs, sig2))
		rmu, err := sk.Sign(nil, mu, crypto.MLDSAMu)
		fmt.Println(" HEDGEDMU", es(err), es(mldsa.Verify(pk, msg, rmu, nil)))
		gk, err := mldsa.GenerateKey(p)
		gs, _ := gk.SignDeterministic(msg, nil)
		fmt.Println(" GEN", es(err), len(gk.Bytes()), gk.Equal(sk), es(mldsa.Verify(gk.PublicKey(), msg, gs, nil)),
			es(mldsa.Verify(pk, msg, gs, nil)))
		for i := 0; i < 4; i++ {
			m := make([]byte, i*37)
			for j := range m {
				m[j] = byte(j*i + pi)
			}
			c := &mldsa.Options{Context: strings.Repeat("c", i*3)}
			s, err := sk.SignDeterministic(m, c)
			fmt.Println(" ACC", i, es(err), h(s), es(mldsa.Verify(pk, m, s, c)))
		}
		fmt.Println(" CROSS", es(mldsa.Verify(sko.PublicKey(), msg, sig, nil)))
	}
	_, err := mldsa.GenerateKey(mldsa.Parameters{})
	fmt.Println("BADPARAMS", es(err))
	_, err = mldsa.NewPrivateKey(mldsa.Parameters{}, make([]byte, 32))
	fmt.Println("BADPARAMS2", es(err))
	_, err = mldsa.NewPublicKey(mldsa.Parameters{}, nil)
	fmt.Println("BADPARAMS3", es(err))
}
