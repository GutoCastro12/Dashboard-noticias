# -*- coding: utf-8 -*-
"""4I.2 §2 — converte GLOBAL_SEMANTIC_AUDIT_4I.csv no gold set EXECUTAVEL.

Preserva apenas os campos necessarios para reproduzir a decisao (titulo,
summary, metadados de fonte/data e empresa monitorada) — nunca o texto
integral das materias. O gold guarda os DOIS lados: as 117 ocorrencias
erradas (que nao podem voltar) e as 115 corretas (que nao podem sumir).
"""
import csv, json
from collections import Counter, defaultdict

AUD = r"C:\Users\Gustavo\DashRisk-semantic-audit\out_semantic_audit_4i\GLOBAL_SEMANTIC_AUDIT_4I.csv"
DEST = r"C:\Users\Gustavo\DashRisk-semantic-fixes\test_fixtures_4i\gold_set_4i.json"

rows = list(csv.DictReader(open(AUD, encoding="utf-8-sig")))

# veredito -> (scoreable_esperado, bucket_esperado, tipo_de_assercao)
#   keep    : deve continuar pontuando exatamente como esta (regressao positiva)
#   drop    : nao pode pontuar para ESTA empresa (sujeito/relacao/negacao/historico)
#   reclass : nao pode pontuar com ESTE event_id (objeto/evento errado)
#   phase   : nao pode pontuar com peso de fato consumado (fase juridica)
#   dedup   : pode pontuar, mas o grupo inteiro colapsa em 1 ocorrencia
#   skip    : indecidivel pela evidencia — fora do pass/fail
MAP = {
    "CORRECT":                            (True,  None,            "keep"),
    "WRONG_SUBJECT":                      (False, "nao_pontuavel", "drop"),
    "WRONG_RELATION":                     (False, "nao_pontuavel", "drop"),
    "NEGATED_EVENT":                      (False, "nao_pontuavel", "drop"),
    "HISTORICAL_REFERENCE":               (False, "nao_pontuavel", "drop"),
    "HISTORICAL_MA":                      (False, "nao_pontuavel", "drop"),
    "RESOLUTION_OF_PRIOR_NEGATIVE_EVENT": (False, "informativo",   "drop"),
    "WRONG_EVENT":                        (False, "reclassificar", "reclass"),
    "WRONG_LEGAL_PHASE":                  (False, "informativo",   "phase"),
    "DUPLICATE_OCCURRENCE":               (True,  None,            "dedup"),
    "NEEDS_MANUAL_REVIEW":                (None,  None,            "skip"),
}

gold = []
for i, r in enumerate(rows, 1):
    if r["bucket"] != "pontuavel":
        # contexto/informativo entram so como regressao de bucket
        gold.append({
            "id": f"G{i:03d}", "assertion": "bucket_only",
            "monitored_company": r["monitored_company"],
            "title": r["title"], "summary": r["hist_summary"],
            "source": r["source"], "domain": r["domain"],
            "forced_trust": r["hist_forced_trust"], "language": r["hist_language"],
            "pub_iso": r["hist_pub_iso"], "pub_ts": r["hist_pub_ts"],
            "captured_ts": r["hist_captured_ts"],
            "current_bucket": r["bucket"], "current_event_id": r["event_id"],
            "expected_bucket": r["bucket"],
            "expected_scoreable": False,
            "audit_verdict": r["veredito"], "root_cause": r["causa_raiz"],
            "rationale": r["motivo_revisao"],
        })
        continue
    sc, bucket, atype = MAP[r["veredito"]]
    gold.append({
        "id": f"G{i:03d}", "assertion": atype,
        "monitored_company": r["monitored_company"],
        "title": r["title"], "summary": r["hist_summary"],
        "source": r["source"], "domain": r["domain"],
        "forced_trust": r["hist_forced_trust"], "language": r["hist_language"],
        "pub_iso": r["hist_pub_iso"], "pub_ts": r["hist_pub_ts"],
        "captured_ts": r["hist_captured_ts"],
        "current_bucket": r["bucket"], "current_event_id": r["event_id"],
        "current_severity": r["severity"], "current_base_weight": r["base_weight"],
        "current_subject_company": r["subject_company"],
        "current_relation_type": r["relation_type"],
        "current_legal_status": r["legal_status"],
        "current_event_phase": r["event_phase"],
        "current_n_sources": r["n_sources"],
        "expected_scoreable": sc,
        "expected_bucket": bucket,
        "expected_event_id": r["event_id"] if r["veredito"] == "CORRECT" else "",
        "forbidden_event_id": "" if r["veredito"] == "CORRECT" else r["event_id"],
        "ma_role_real": r["ma_role"], "legal_phase_real": r["legal_phase_real"],
        "audit_verdict": r["veredito"], "root_cause": r["causa_raiz"],
        "rationale": r["motivo_revisao"],
        "appears_in_windows": r["appears_in_windows"],
    })

# grupos de dedup: mesma empresa + mesma familia + mesmo fato economico
grupos = defaultdict(list)
for g in gold:
    if g["assertion"] == "dedup":
        grupos[(g["monitored_company"], g["current_event_id"])].append(g["id"])
for g in gold:
    if g["assertion"] == "dedup":
        g["dedup_group"] = f"{g['monitored_company']}|{g['current_event_id']}"

import os
os.makedirs(os.path.dirname(DEST), exist_ok=True)
json.dump({
    "_meta": {
        "origem": "auditoria 4I (GLOBAL_SEMANTIC_AUDIT_4I.csv), HEAD 513f3cc, commit 9e91611",
        "janelas_auditadas": ["7", "30", "90", "365"],
        "total": len(gold),
        "principio": ("gold guarda os DOIS lados: os erros nao podem voltar E os "
                       "acertos nao podem sumir (4I.2 §3)"),
    },
    "casos": gold,
}, open(DEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

c = Counter(g["assertion"] for g in gold)
print("GOLD SET 4I gerado:", DEST)
print("total de casos:", len(gold))
for k, v in c.most_common():
    print(f"  {k:12s} {v}")
pont = [g for g in gold if g["assertion"] != "bucket_only"]
print(f"\npontuaveis no gold: {len(pont)}")
print(f"  POSITIVOS (keep, nao podem sumir): {sum(1 for g in pont if g['assertion']=='keep')}")
print(f"  NEGATIVOS (nao podem voltar):      "
      f"{sum(1 for g in pont if g['assertion'] in ('drop','reclass','phase'))}")
print(f"  DEDUP (colapsar em 1):             {sum(1 for g in pont if g['assertion']=='dedup')}")
print(f"  SKIP (indecidivel):                {sum(1 for g in pont if g['assertion']=='skip')}")
print(f"\ngrupos de dedup: {len(grupos)}")
for k, v in sorted(grupos.items())[:8]:
    print(f"    {k[0]} | {k[1]}: {len(v)} ocorrencia(s) marcada(s) duplicata")
