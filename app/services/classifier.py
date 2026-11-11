"""Guardian - inferensi IndoBERTweet + BiLSTM untuk backend Flask.

Arsitektur dan preprocessing di sini HARUS identik dengan notebook training.
Preprocessing tinggal di ``app/services/preprocessing.py`` (satu sumber
kebenaran, diuji oleh ``tests/test_preprocessing.py``).

Letakkan bobot di ``<MODEL_DIR>/<MODEL_FILE>`` (default ``models/best_weights.pt``).

Pemakaian:
    from app.services.classifier import get_classifier
    clf = get_classifier(config)          # dimuat SEKALI, saat server start
    clf.predict('dasar b0d0h lu @budi')
    # {'label': 'bully', 'label_id': 1, 'prob_bully': 0.97, ...}
"""
import logging
import threading
from pathlib import Path

import torch
import torch.nn as nn
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence
from transformers import AutoModel, AutoTokenizer

from app.services.preprocessing import light_preprocess

logger = logging.getLogger(__name__)

# ── Konfigurasi: HARUS sama dengan notebook training ──
MODEL_NAME = 'indolem/indobertweet-base-uncased'
MAX_LENGTH = 128
LSTM_HIDDEN = 128
NUM_LABELS = 2
DROPOUT = 0.5            # tidak berpengaruh saat eval(), dipertahankan agar struktur identik
TARGET_NAMES = ['non-bully', 'bully']   # 0 = non-bully, 1 = bully

# Bobot kepala klasifikasi. Kalau salah satu ini tidak ada di checkpoint,
# model akan memprediksi dengan bobot acak -> harus gagal keras, bukan diam.
_CRITICAL_PREFIXES = ('lstm.', 'classifier.')


class GuardianModel(nn.Module):
    """Salinan EncoderClassifier dari notebook (head='bilstm')."""

    def __init__(self, encoder: nn.Module):
        super().__init__()
        self.encoder = encoder
        H = encoder.config.hidden_size
        self.lstm = nn.LSTM(H, LSTM_HIDDEN, 1, bidirectional=True, batch_first=True)
        self.dropout = nn.Dropout(DROPOUT)
        self.classifier = nn.Linear(LSTM_HIDDEN * 2, NUM_LABELS)

    @staticmethod
    def masked_mean_pool(h, mask):
        m = mask.unsqueeze(-1).to(h.dtype)
        return (h * m).sum(1) / m.sum(1).clamp(min=1e-9)

    def forward(self, input_ids, attention_mask):
        h = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        total_len = h.size(1)
        lengths = attention_mask.sum(1).clamp(min=1).cpu()
        packed = pack_padded_sequence(h.float(), lengths, batch_first=True, enforce_sorted=False)
        out, _ = self.lstm(packed)
        out, _ = pad_packed_sequence(out, batch_first=True, total_length=total_len)
        pooled = self.masked_mean_pool(out, attention_mask)
        return self.classifier(self.dropout(pooled))


class GuardianClassifier:
    def __init__(self, weights_path: str, device: str | None = None):
        path = Path(weights_path)
        if not path.is_file():
            tersedia = sorted(p.name for p in path.parent.glob('*.pt')) if path.parent.is_dir() else []
            raise FileNotFoundError(
                f'Bobot model tidak ditemukan: {path}. '
                f"File .pt di folder itu: {tersedia or 'tidak ada'}. "
                'Atur MODEL_DIR / MODEL_FILE di .env.'
            )

        # fp32 saja. fp16 lebih lambat di GPU lama dan tidak dipakai saat training.
        self.device = torch.device(device or ('cuda' if torch.cuda.is_available() else 'cpu'))
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

        # from_pretrained (bukan from_config): dipasangkan dengan strict=False,
        # kunci encoder yang tidak ada di checkpoint tetap berisi bobot
        # IndoBERTweet asli, bukan angka acak.
        encoder = AutoModel.from_pretrained(MODEL_NAME)
        self.model = GuardianModel(encoder)

        state = torch.load(str(path), map_location='cpu', weights_only=True)
        # strict=False disengaja: checkpoint boleh tidak membawa buffer non-bobot
        # (mis. position_ids) tanpa menggagalkan pemuatan.
        result = self.model.load_state_dict(state, strict=False)

        hilang_kritis = [k for k in result.missing_keys if k.startswith(_CRITICAL_PREFIXES)]
        if hilang_kritis:
            raise RuntimeError(
                f'Checkpoint {path.name} tidak memuat bobot kepala klasifikasi: '
                f'{hilang_kritis}. Model akan menebak acak, pemuatan dibatalkan.'
            )
        if result.missing_keys:
            logger.warning('Checkpoint tidak memuat %d kunci (pakai bobot pretrained): %s',
                           len(result.missing_keys), result.missing_keys[:10])
        if result.unexpected_keys:
            logger.warning('Checkpoint punya %d kunci tak dikenal (diabaikan): %s',
                           len(result.unexpected_keys), result.unexpected_keys[:10])

        self.model.to(self.device).eval()
        self._lock = threading.Lock()   # Flask threaded: satu forward pass per waktu
        logger.info('Classifier siap: %s device=%s', path.name, self.device)

    @staticmethod
    def _keyakinan(p_max: float) -> str:
        # Pita yang sama dengan tabel_prediksi() di notebook
        if p_max <= 0.65:
            return 'ragu'
        if p_max <= 0.80:
            return 'sedang'
        return 'tinggi'

    @torch.no_grad()
    def predict_batch(self, texts: list[str]) -> list[dict]:
        clean = [light_preprocess(t) for t in texts]
        enc = self.tokenizer(clean, max_length=MAX_LENGTH, padding=True,
                             truncation=True, add_special_tokens=True,
                             return_tensors='pt').to(self.device)
        with self._lock:
            logits = self.model(enc['input_ids'], enc['attention_mask']).float()
        probs = torch.softmax(logits, dim=1).cpu()

        hasil = []
        for p in probs:
            p_non, p_bul = float(p[0]), float(p[1])
            label_id = int(p.argmax())        # argmax = ambang 0.5, sama seperti evaluasi notebook
            hasil.append({
                'label': TARGET_NAMES[label_id],
                'label_id': label_id,
                'prob_nonbully': round(p_non, 4),
                'prob_bully': round(p_bul, 4),
                'keyakinan': self._keyakinan(max(p_non, p_bul)),
            })
        return hasil

    def predict(self, text: str) -> dict:
        return self.predict_batch([text])[0]


# ── Singleton untuk Flask: model dimuat sekali ──
_instance: GuardianClassifier | None = None
_instance_lock = threading.Lock()


def get_classifier(config) -> GuardianClassifier:
    """Return the shared classifier, loading ``<MODEL_DIR>/<MODEL_FILE>`` on first use."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                path = Path(config.MODEL_DIR)
                if not path.is_absolute():
                    path = Path(__file__).resolve().parents[2] / path
                _instance = GuardianClassifier(str(path / config.MODEL_FILE))
    return _instance
