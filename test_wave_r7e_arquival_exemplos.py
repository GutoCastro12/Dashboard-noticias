#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wave_r7e_arquival_exemplos.py — R7e.

O ACERVO VIVO NÃO É ENTRADA DE ARQUIVO.

A camada arquival já havia desacoplado a VERDADE HUMANA do congelamento
(`occurrence_auditor_dev_truth_snapshot_v1`). Ficou faltando a outra entrada:
`exemplos_congelados()` reconstrói o payload dos exemplos a partir de
`risk_history.json`, que também cresce e também é corrigido.

O que expôs isso foi uma correção de produção legítima. O artigo da BRF sobre o
adiamento da assembleia estava gravado com a data do feed (2026-05-28) quando a
própria página declara 2025-06-17; reparar a data (`f62199e`) mudou o payload do
exemplo da BRF e derrubou `example_set_hash` e `freeze_manifest_hash` — sem que
V1, V2 ou V3 tivessem mudado um byte.

Nenhum hash histórico foi reescrito para ficar verde. O alvo continua sendo
`6ea9a6519b3066bb`; o que mudou foi a ENTRADA voltar a ser a histórica.

O teste central é metamórfico (BLOCO C): mutar o acervo vivo — inclusive nos
campos que causaram o incidente — não pode mover nenhum hash congelado.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os

import reliability_occurrence_archival_verifier as av
import reliability_occurrence_auditor_freeze as v1
import reliability_occurrence_auditor_freeze_v2 as v2
import reliability_occurrence_auditor_freeze_v3 as v3
import reliability_occurrence_auditor_pilot_v3 as p3
import semantic_v2_shadow as sh

PASS = FAIL = 0
BRF_ID = "972a2d5f184545235f9d"
EXAMPLE_HISTORICO = "6ea9a6519b3066bb"


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {label}")
    else:
        FAIL += 1
        print(f"  FALHOU: {label}")


SNAP = av.carregar_snapshot()
HIST = av.SNAPSHOT_HISTORICO


def _hash_exemplos(historico):
    ex = v3.exemplos_congelados(json.loads(json.dumps(SNAP)), historico=historico)
    return av._hash({k: v["prompt_payload"] for k, v in ex.items()})


print("=" * 98)
print("BLOCO A - o snapshot historico existe, e imutavel e se declara")
print("=" * 98)
_snap_h = json.loads(io.open(HIST, encoding="utf-8").read())
_meta = _snap_h["_meta"]
check(av.checksum_snapshot(HIST) == av.SNAPSHOT_HISTORICO_SHA256,
      f"[1] checksum bate com o literal do verificador ({av.SNAPSHOT_HISTORICO_SHA256})")
check(_meta.get("role") == "HISTORICAL_FREEZE_INPUT",
      "[2] declara-se entrada historica de congelamento")
check(_meta.get("current_authority") == "NONE", "[3] autoridade corrente NENHUMA")
check(_meta.get("production_authority") == "NONE", "[4] autoridade de producao NENHUMA")
check("363d9c8" in (_meta.get("provenance") or ""),
      "[5] a proveniencia nomeia o commit de origem, sem fingir anterioridade")
check("occurrence_truth" not in _snap_h and "observacoes" not in _snap_h,
      "[6] NAO duplica o namespace de verdade humana")
check(len(_snap_h["articles"]) < 200,
      f"[7] e um artefato pequeno, nao uma segunda base ({len(_snap_h['articles'])} artigos)")

print()
print("=" * 98)
print("BLOCO B - a reconstrucao historica bate EXATAMENTE")
print("=" * 98)
_h = _hash_exemplos(HIST)
check(_h == EXAMPLE_HISTORICO,
      f"[8] example_set_hash reconstruido = {EXAMPLE_HISTORICO} (obtido {_h})")
check(av.EXAMPLE_SET_HISTORICO == EXAMPLE_HISTORICO,
      "[9] o verificador continua esperando o hash HISTORICO")
check(EXAMPLE_HISTORICO in io.open(
    "reliability_occurrence_auditor_freeze_v3.py", encoding="utf-8").read(),
    "[10] o literal congelado na V3 nao foi reescrito")
_viva = _hash_exemplos("risk_history.json")
check(_viva != EXAMPLE_HISTORICO,
      f"[11] e o acervo VIVO produz outro hash ({_viva}) - era esse o defeito")
for nome, mod in (("V1", v1), ("V2", v2), ("V3", v3)):
    _d = mod.verificar_congelamento(json.loads(json.dumps(SNAP)), historico=HIST)
    check(not _d, f"[12..14] {nome} sem divergencia contra o snapshot historico ({_d})")

print()
print("=" * 98)
print("BLOCO C - METAMORFICO: mutar o acervo vivo nao move hash congelado")
print("=" * 98)
_VIVO = json.load(io.open("risk_history.json", encoding="utf-8"))


def _brf(Hx):
    for u, r in Hx["articles"].items():
        if sh.id_artigo(r.get("url") or u, r.get("title") or "") == BRF_ID:
            return r
    return None


def _com_mutacao(fn):
    """Aplica `fn` a uma COPIA do acervo vivo; o arquivo real nunca e tocado."""
    Hx = copy.deepcopy(_VIVO)
    fn(Hx)
    tmp = "_r7e_tmp_hist.json"
    io.open(tmp, "w", encoding="utf-8").write(json.dumps(Hx, ensure_ascii=False))
    try:
        h = _hash_exemplos(HIST)
        d3 = v3.verificar_congelamento(json.loads(json.dumps(SNAP)), historico=HIST)
        return h, d3, None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _m1(H):
    _brf(H).update({"pub_ts": 1700000000, "pub_iso": "2023-11-14 22:13"})


def _m2(H):
    _brf(H).update({"pub_date_origin": "feed",
                    "pub_date_verification": "verificado_sem_conflito",
                    "pub_date_conflict_s": 0})


def _m3(H):
    _brf(H).update({"title": "TITULO TROCADO PARA TESTE"})


def _m4(H):
    _brf(H).update({"events_by_company": {}, "event_ids": []})


def _m5(H):
    for u, r in list(H["articles"].items()):
        if sh.id_artigo(r.get("url") or u, r.get("title") or "") == BRF_ID:
            H["articles"].pop(u)


_MUT = [("M1 data da BRF movida de novo (o incidente exato)", _m1),
        ("M2 proveniencia de data da BRF reescrita", _m2),
        ("M3 titulo da BRF alterado", _m3),
        ("M4 evento da BRF removido", _m4),
        ("M5 registro da BRF apagado do acervo vivo", _m5)]
for rot, fn in _MUT:
    h, d3, h_mut = _com_mutacao(fn)
    check(h == EXAMPLE_HISTORICO and not d3,
          f"[15..19] {rot}: example_set_hash intacto ({h})")

print()
print("=" * 98)
print("BLOCO D - crescimento do cron nao pode quebrar o arquivo")
print("=" * 98)


def _cresce(Hx):
    Hx["articles"]["https://exemplo.invalido/novo-artigo-do-cron"] = {
        "url": "https://exemplo.invalido/novo-artigo-do-cron",
        "domain": "exemplo.invalido", "source": "Fonte Nova",
        "title": "BRF anuncia nova aquisicao bilionaria nesta segunda",
        "summary": "", "pub_ts": 1790000000, "pub_iso": "2026-09-20 12:00",
        "companies": ["BRF"], "event_ids": ["ma"],
        "events_by_company": {"BRF": ["ma"]}}


h, d3, _ = _com_mutacao(_cresce)
check(h == EXAMPLE_HISTORICO and not d3,
      f"[20] artigo novo de emissor curado nao move o hash ({h})")

print()
print("=" * 98)
print("BLOCO E - correcao legitima futura em emissor curado")
print("=" * 98)


def _fabrica(emp):
    def _corrige(Hx):
        for u, r in Hx["articles"].items():
            if emp in (r.get("companies") or []):
                r["pub_ts"] = 1700000000
                r["pub_iso"] = "2023-11-14 22:13"
    return _corrige


for emp, rot in (("Hapvida", "M6"), ("Santander Brasil", "M7"), ("Smart Fit", "M8")):
    h, d3, _ = _com_mutacao(_fabrica(emp))
    check(h == EXAMPLE_HISTORICO and not d3,
          f"[21..23] {rot} correcao de data em {emp} nao move o hash ({h})")

print()
print("=" * 98)
print("BLOCO F - DUPLA VERDADE: producao corrigida x exemplo congelado")
print("=" * 98)
_ex = v3.exemplos_congelados(json.loads(json.dumps(SNAP)), historico=HIST)
_payload_brf = json.dumps(_ex["BRF"]["prompt_payload"], ensure_ascii=False)
_rec_vivo = _brf(_VIVO)
check("2026-05-28" in _payload_brf,
      "[24] o exemplo CONGELADO guarda o input que a V3 viu (2026-05-28)")
check("2025-06-17" not in _payload_brf,
      "[25] e NAO foi atualizado com a data corrigida depois")
check(bool(_rec_vivo) and str(_rec_vivo.get("pub_iso", "")).startswith("2025-06-17"),
      f"[26] a PRODUCAO guarda a data verdadeira "
      f"({_rec_vivo.get('pub_iso') if _rec_vivo else '-'})")
check(bool(_rec_vivo) and _rec_vivo.get("pub_date_origin") == "pagina",
      "[27] com a proveniencia da pagina preservada")
check(bool(_rec_vivo) and _rec_vivo.get("feed_pub_iso") == "2026-05-28 09:24",
      "[28] e a data do feed preservada para auditoria")
check(bool(_rec_vivo) and "2026-05-28" in _payload_brf
      and _rec_vivo.get("pub_iso") != "2026-05-28 09:24",
      "[29] as duas verdades coexistem sem uma sobrescrever a outra")

print()
print("=" * 98)
print("BLOCO G - bytes congelados intactos")
print("=" * 98)
_ESPERADO = {
    "reliability_occurrence_auditor_freeze.py": "69e1229b1ceb6153",
    "reliability_occurrence_auditor_freeze_v2.py": "f0aa9ab28a9f24be",
    "reliability_occurrence_auditor_freeze_v3.py": "48b66a8b4e7ae166",
    "reliability_occurrence_auditor_pilot.py": "e1bd1409da2f37ab",
    "reliability_occurrence_auditor_pilot_v2.py": "cef26c484c5e1647",
    "reliability_occurrence_auditor_pilot_v3.py": "03ec1bda5ca68c6a",
    "reliability_occurrence_auditor_input.py": "fc2520b80238e241",
    "occurrence_auditor_dev_truth_snapshot_v1.json": "622082c16255ce7a",
}
for f, esperado in sorted(_ESPERADO.items()):
    obtido = hashlib.sha256(io.open(f, "rb").read()).hexdigest()[:16]
    check(obtido == esperado, f"[30..37] {f} inalterado ({obtido})")

print()
print("=" * 98)
print("BLOCO H - o verificador arquival de ponta a ponta")
print("=" * 98)
rel = av.verificar_historico()
check(rel["ok"], f"[38] arquival OK ({rel['problemas']})")
for n in ("V1", "V2", "V3"):
    check(rel["por_experimento"][n]["ok"], f"[39..41] {n} PASS")
check(rel["dev_manifest_hash"] == av.MANIFESTO_HISTORICO,
      f"[42] dev_manifest historico preservado ({rel['dev_manifest_hash']})")
check(rel["snapshot_historico_checksum"] == av.SNAPSHOT_HISTORICO_SHA256,
      "[43] o relatorio expoe o checksum do snapshot historico")
check(rel["verifier_version"] == "occurrence.auditor.archival.v2",
      f"[44] versao do verificador anunciada ({rel['verifier_version']})")
check(len(p3.alvos_congelados(SNAP)) == 17,
      "[45] devset V3 continua com 17 alvos")

print()
print("=" * 98)
print(f"RESULTADO WAVE R7e (exemplos arquivais imutaveis): "
      f"{PASS}/{PASS+FAIL} checagens passaram")
print("=" * 98)
if FAIL:
    raise SystemExit(1)
