"""Text preprocessing for the classification pipeline.

PERINGATAN
----------
Isi modul ini HARUS identik byte-per-byte secara perilaku dengan
``light_preprocess`` yang dipakai saat training model. Setiap perbedaan
menimbulkan train/serve skew: model menerima bentuk teks yang tidak pernah
dilihatnya saat belajar, dan akurasi turun tanpa gejala error apa pun.
Jangan "merapikan" fungsi di bawah tanpa melatih ulang model.

Urutan operasi (tidak boleh ditukar):
1. lower()
2. URL -> HTTPURL
3. @mention -> @USER
4. normalize_obfuscation()  (de-leet, un-spacing, squeeze huruf berulang)
5. emoji.demojize(language="id")
6. normalisasi whitespace

Langkah 2 dan 3 sengaja mendahului langkah 4: token penggantinya huruf besar
(``HTTPURL``, ``@USER``) sehingga kebal terhadap de-leet, yang hanya menyentuh
token berawalan huruf kecil.
"""

import re

import emoji

# Substitusi leetspeak yang dikenali. Hanya berlaku di TENGAH kata.
_LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "@": "a", "$": "s"}

# Kata yang dipecah spasi/titik/strip, mis. "b o d o h" atau "b.o.d.o.h".
_SPACED_OBF = re.compile(r"(?:(?<=\s)|^)((?:[a-z][ .\-]){2,}[a-z])(?=\s|$|[.,!?])")

_TOKEN_RE = re.compile(r"[a-z][a-z0-9@$]*")
_REPEAT_RE = re.compile(r"([a-z])\1{2,}")
_URL_RE = re.compile(r"http\S+|www\.\S+")
_MENTION_RE = re.compile(r"@\w+")
_WHITESPACE_RE = re.compile(r"\s+")


def _deleet_token(w: str) -> str:
    """Ganti angka/simbol leet di tengah kata, hanya jika diapit huruf.

    Dibatasi ke posisi tengah supaya angka yang berdiri sendiri ("4", "2024")
    dan awalan/akhiran numerik tidak ikut berubah.

    >>> _deleet_token("b0d0h")
    'bodoh'
    >>> _deleet_token("g0")
    'g0'
    """
    cs = list(w)
    for i in range(1, len(cs) - 1):
        if cs[i] in _LEET and cs[i - 1].isalpha() and cs[i + 1].isalpha():
            cs[i] = _LEET[cs[i]]
    return "".join(cs)


def normalize_obfuscation(text: str) -> str:
    """Buka penyamaran kata: spasi-sisip, leetspeak, dan huruf berulang.

    >>> normalize_obfuscation("b o d o h")
    'bodoh'
    >>> normalize_obfuscation("b0d0h")
    'bodoh'
    >>> normalize_obfuscation("bodohhhhh")
    'bodohh'
    """
    text = _SPACED_OBF.sub(lambda m: re.sub(r"[ .\-]", "", m.group(1)), text)
    text = _TOKEN_RE.sub(lambda m: _deleet_token(m.group(0)), text)
    return _REPEAT_RE.sub(r"\1\1", text)


def light_preprocess(text) -> str:
    """Bersihkan *text* persis seperti pipeline training.

    >>> light_preprocess("Halo @Budi lihat https://t.co/abc  !")
    'halo @USER lihat HTTPURL !'
    >>> light_preprocess("DASAR B0D0H!!")
    'dasar bodoh!!'
    """
    text = str(text).lower()
    text = _URL_RE.sub("HTTPURL", text)
    text = _MENTION_RE.sub("@USER", text)
    text = normalize_obfuscation(text)
    text = emoji.demojize(text, language="id")
    return _WHITESPACE_RE.sub(" ", text).strip()


# Nama lama, dipertahankan supaya pemanggil yang sudah ada tidak putus.
preprocess = light_preprocess
