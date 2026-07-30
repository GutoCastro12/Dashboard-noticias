#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edgar_audit_4h2.py — Auditoria diagnóstica do coletor EDGAR (Fase 4H.2, Bloco A–D).

Este módulo é ESTRITAMENTE DIAGNÓSTICO. Não altera histórico de produção,
score, pesos, thresholds, taxonomia, tiers nem publica dashboard. Não executa
backfill. Todas as funções de parsing são isoladas da rede e testáveis via
fixtures.

Descoberta central (causa raiz do "0 filings" nos 32 emissores elegíveis):

    No config de produção, `official_sources.EUA.formularios_gatilho` está
    gravado como a STRING  "['8-K', '6-K', '10-K', '10-Q', '20-F']"  (aspas
    externas), não como uma lista YAML. O coletor faz:

        forms = set(src.get("formularios_gatilho") or [...])

    Aplicar `set(...)` a uma STRING itera sobre CARACTERES, produzindo o
    conjunto {'[', "'", '8', '-', 'K', ',', ' ', '6', '1', '0', '2', 'F',
    'Q', ']'} (14 caracteres). Em `_edgar_articles_from_submissions`, o filtro
    `if form not in forms: continue` então rejeita TODO formulário real
    ("8-K", "6-K", "20-F", ...), porque nenhuma string multi-caractere pertence
    a um conjunto de caracteres únicos. Resultado: 0 aceitos para TODOS os 32,
    apesar de HTTP 200 + JSON válido. A telemetria de produção confirma:
    attempted=32, success=32, http=200, filings_found=0 (uniforme).

A correção mínima é normalizar `formularios_gatilho` (aceitar list | string de
lista | csv) ANTES do `set(...)`. Ver `normalize_edgar_forms`.
"""
from __future__ import annotations

import ast
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Formulários materialmente relevantes ao radar, por tipo de emissor.
# Domésticos EUA: 8-K (fato relevante), 10-K (anual), 10-Q (trimestral).
# Foreign Private Issuers: 20-F (anual), 6-K (fato relevante/interino).
# MJDS canadense (ex.: Toronto-Dominion): 40-F (anual), 6-K.
DEFAULT_EDGAR_FORMS = ["8-K", "6-K", "10-K", "10-Q", "20-F", "40-F"]

STAGES = ["raw_submissions", "date_filtered", "form_filtered",
          "issuer_filtered", "parsed", "deduplicated", "accepted"]


# ───────────────────────── correção da causa raiz ─────────────────────────
def normalize_edgar_forms(raw) -> set[str]:
    """Converte `formularios_gatilho` em um set de formulários REAIS.

    Aceita:
      - list/tuple/set já correta;
      - string de lista Python/JSON: "['8-K','6-K']" ou '["8-K","6-K"]';
      - string CSV: "8-K, 6-K, 20-F";
      - None → allowlist padrão (DEFAULT_EDGAR_FORMS).

    NUNCA faz `set(<str>)` diretamente (esse era o bug: gerava conjunto de
    caracteres). Sempre normaliza para tokens antes.
    """
    if raw is None:
        return set(DEFAULT_EDGAR_FORMS)
    if isinstance(raw, (list, tuple, set)):
        items = [str(x).strip() for x in raw]
        return {x for x in items if x}
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return set(DEFAULT_EDGAR_FORMS)
        parsed = None
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = ast.literal_eval(s)
            except Exception:
                try:
                    parsed = json.loads(s)
                except Exception:
                    parsed = None
        if isinstance(parsed, (list, tuple, set)):
            return {str(x).strip() for x in parsed if str(x).strip()}
        # fallback CSV / lista mal-formada: separa por vírgula e limpa aspas/colchetes
        toks = [t.strip().strip("'\"[] ") for t in s.split(",")]
        toks = [t for t in toks if t]
        return set(toks) if toks else set(DEFAULT_EDGAR_FORMS)
    # tipo inesperado
    t = str(raw).strip()
    return {t} if t else set(DEFAULT_EDGAR_FORMS)


def buggy_forms_charset(raw) -> set[str]:
    """Reproduz EXATAMENTE o comportamento defeituoso de produção:
    `set(<valor>)`. Usado apenas na auditoria para demonstrar a causa raiz."""
    default = ["8-K", "6-K", "10-K", "20-F"]
    return set(raw or default)


def form_matches(form: str, forms: set[str], match_amendments: bool = False) -> bool:
    """Correspondência de formulário. Exata por padrão (respeita 'não ampliar
    a allowlist cegamente'). Com match_amendments=True, um '8-K/A' casa se
    '8-K' estiver na allowlist (recomendação para 4H.3, desligado aqui)."""
    if form in forms:
        return True
    if match_amendments and form.endswith("/A"):
        return form[:-2] in forms
    return False


# ───────────────────── contagem por estágio (Bloco A) ─────────────────────
def edgar_stage_counts(data: dict, forms: set[str], cutoff_ts: int,
                       match_amendments: bool = False) -> dict:
    """Contagem por estágio do pipeline EDGAR para UM emissor.

    Não registra apenas accepted=0: devolve o funil completo
    (raw_submissions → date_filtered → form_filtered → issuer_filtered →
    parsed → deduplicated → accepted), permitindo localizar exatamente onde
    o filing é perdido.

    Observação: o endpoint submissions é POR CIK; todos os registros já
    pertencem ao emissor consultado. Portanto issuer_filtered == form_filtered
    aqui (a coluna existe para paridade com o pipeline e para o caso, tratado
    à parte, de CIK apontar para a entidade errada — ver audit_issuer_row)."""
    recent = ((data.get("filings") or {}).get("recent") or {})
    fs = recent.get("form", []) or []
    dates = recent.get("filingDate", []) or []
    accs = recent.get("accessionNumber", []) or []
    docs = recent.get("primaryDocument", []) or []

    raw = len(fs)
    date_ok, forms_seen, forms_dropped = [], {}, {}
    for i, f in enumerate(fs):
        forms_seen[f] = forms_seen.get(f, 0) + 1
        try:
            ts = int(datetime.strptime(dates[i], "%Y-%m-%d")
                     .replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            continue
        date_ok.append((i, f, ts))
    date_filtered = [t for t in date_ok if t[2] >= cutoff_ts]

    form_filtered = []
    for i, f, ts in date_filtered:
        if form_matches(f, forms, match_amendments):
            form_filtered.append((i, f, ts))
        else:
            forms_dropped[f] = forms_dropped.get(f, 0) + 1

    issuer_filtered = form_filtered  # submissions já é por CIK
    parsed = list(issuer_filtered)   # metadados sempre presentes no submissions

    seen, dedup = set(), []
    for i, f, ts in parsed:
        acc = (accs[i] if i < len(accs) else "").replace("-", "")
        key = acc or f"{f}:{ts}:{i}"
        if key in seen:
            continue
        seen.add(key)
        dedup.append((i, f, ts))

    return {
        "raw_submissions": raw,
        "date_filtered": len(date_filtered),
        "form_filtered": len(form_filtered),
        "issuer_filtered": len(issuer_filtered),
        "parsed": len(parsed),
        "deduplicated": len(dedup),
        "accepted": len(dedup),
        "_forms_seen": forms_seen,
        "_forms_dropped_by_form_filter": forms_dropped,
    }


def sample_filings(data: dict, forms: set[str], cutoff_ts: int, cik10: str,
                   company: str, limit: int = 3, match_amendments: bool = False) -> list[dict]:
    """Extrai até `limit` filings recentes aceitos, com accession + URL direta.
    Usado por filings_edgar_amostra.csv (Bloco C)."""
    recent = ((data.get("filings") or {}).get("recent") or {})
    fs = recent.get("form", []) or []
    dates = recent.get("filingDate", []) or []
    accs = recent.get("accessionNumber", []) or []
    docs = recent.get("primaryDocument", []) or []
    descs = recent.get("primaryDocDescription", []) or []
    out = []
    for i, f in enumerate(fs):
        try:
            ts = int(datetime.strptime(dates[i], "%Y-%m-%d")
                     .replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            continue
        if ts < cutoff_ts:
            continue
        if not form_matches(f, forms, match_amendments):
            continue
        acc_raw = accs[i] if i < len(accs) else ""
        acc = acc_raw.replace("-", "")
        doc = docs[i] if i < len(docs) else ""
        try:
            url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{acc}/{doc}"
                   if acc and doc else
                   f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik10}")
        except Exception:
            url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik10}"
        out.append({
            "emissor": company, "cik": cik10, "formulario": f,
            "data": dates[i] if i < len(dates) else "",
            "accession_number": acc_raw,
            "descricao": (descs[i] if i < len(descs) else "") or "",
            "url_direta": url,
        })
        if len(out) >= limit:
            break
    return out


# ───────────────────── rede: submissions por CIK (Bloco D) ────────────────
_EDGAR_UA = "Radar de Risco - Vinci Partners (risco@vincipartners.com)"


def fetch_submissions_live(cik10: str, session=None, timeout: int = 25) -> tuple[dict, int, str]:
    """Busca o submissions JSON de UM CIK. Devolve (data, status_code, erro).
    Só é chamado no modo --edgar-dry-run/--audit-edgar com rede disponível."""
    import requests  # import tardio: o módulo é testável sem rede
    sess = session or requests.Session()
    headers = {"User-Agent": _EDGAR_UA, "Accept-Encoding": "gzip, deflate",
               "Host": "data.sec.gov"}
    try:
        r = sess.get(f"https://data.sec.gov/submissions/CIK{cik10}.json",
                     headers=headers, timeout=timeout)
        sc = r.status_code
        r.raise_for_status()
        return r.json(), sc, ""
    except Exception as exc:  # noqa: BLE001
        sc = getattr(getattr(exc, "response", None), "status_code", None)
        return {}, (sc or 0), f"{type(exc).__name__}: {str(exc)[:200]}"


def load_fixture(fixtures_dir: Path, name: str) -> dict | None:
    """Lê um submissions JSON de fixture (offline). Nome = slug do emissor."""
    slug = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
    for cand in (fixtures_dir / f"{slug}.json", fixtures_dir / f"{name}.json"):
        if cand.exists():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


# ───────────────────────── runner de auditoria ───────────────────────────
def _eligible(cfg: dict) -> list[dict]:
    SEC = ("nyse", "nasdaq")
    def elig(c):
        if c.get("cik"):
            return True
        if (c.get("official") or {}).get("sec"):
            return True
        if any(x in (c.get("listing") or "").lower() for x in SEC):
            return True
        return bool(c.get("ticker")) and c.get("country") in ("EUA", "Canadá")
    return [c for c in cfg.get("watchlist", []) if elig(c)]


def run_edgar_audit(cfg: dict, *, fixtures_dir: str | None = None, live: bool = False,
                    run_meta: dict | None = None, outdir: str = ".",
                    cik_map: dict | None = None) -> dict:
    """Executa a auditoria por emissor e por estágio, comparando allowlist
    defeituosa (produção) vs. corrigida. Escreve os CSVs/MD do Bloco J.

    Fontes de dados de submissions, em ordem:
      1) fixtures_dir (offline, para testes/sandbox);
      2) live=True (rede real, ambiente do Gustavo);
      3) fallback: telemetria de run_meta (apenas accepted real; estágios ficam
         'requer_rede_ou_fixture').
    """
    outp = Path(outdir)
    outp.mkdir(parents=True, exist_ok=True)
    fixd = Path(fixtures_dir) if fixtures_dir else None

    src = (cfg.get("official_sources") or {}).get("EUA") or {}
    raw_forms_cfg = src.get("formularios_gatilho")
    forms_fixed = normalize_edgar_forms(raw_forms_cfg)
    forms_buggy = buggy_forms_charset(raw_forms_cfg)
    janela = int((cfg.get("evolution") or {}).get("window_days", 90))
    cutoff = int(datetime.now(timezone.utc).timestamp()) - janela * 86400

    alvos = _eligible(cfg)
    tele = ((run_meta or {}).get("official_source_execution") or {}).get("EDGAR", {})

    per_issuer, per_stage_rows, amostra, sem_resultado = [], [], [], []
    agg_fixed = {s: 0 for s in STAGES}
    agg_buggy = {s: 0 for s in STAGES}
    session = None
    if live:
        try:
            import requests
            session = requests.Session()
        except Exception:
            live = False

    for c in alvos:
        name = c["name"]
        cik10 = c.get("cik")
        if not cik10 and cik_map:
            cik10 = cik_map.get(str(c.get("ticker") or "").upper())
        tl = tele.get(name, {}) if isinstance(tele, dict) else {}

        data, source, status, err = None, "", tl.get("status_code"), ""
        if fixd:
            data = load_fixture(fixd, name)
            if data is not None:
                source = "fixture"
        if data is None and live and cik10:
            data, status, err = fetch_submissions_live(str(cik10), session)
            source = "live"
            time.sleep(0.15)
        # contagens
        if data:
            cf = edgar_stage_counts(data, forms_fixed, cutoff)
            cb = edgar_stage_counts(data, forms_buggy, cutoff)
            for s in STAGES:
                agg_fixed[s] += cf[s]
                agg_buggy[s] += cb[s]
            amostra += sample_filings(data, forms_fixed, cutoff, str(cik10 or ""), name, limit=3)
            forms_seen = cf["_forms_seen"]
            diagnostico = ("corrigido_recupera_filings" if cf["accepted"] > 0 and cb["accepted"] == 0
                           else ("sem_filing_na_janela" if cf["accepted"] == 0 else "ok"))
            per_stage_rows.append({"emissor": name, "allowlist": "corrigida (lista real)", **{s: cf[s] for s in STAGES}})
            per_stage_rows.append({"emissor": name, "allowlist": "defeituosa (set de string)", **{s: cb[s] for s in STAGES}})
        else:
            # sem submissions aqui: usa telemetria real (accepted=0) e marca estágios
            forms_seen = {}
            cf = {s: None for s in STAGES}
            cf["accepted"] = tl.get("filings_found", 0)
            diagnostico = "requer_rede_ou_fixture"
            per_stage_rows.append({"emissor": name, "allowlist": "corrigida (lista real)",
                                   **{s: (cf[s] if cf[s] is not None else "requer_rede_ou_fixture") for s in STAGES}})

        per_issuer.append({
            "emissor": name,
            "pais": c.get("country") or "",
            "tipo_emissor": _classify_issuer_type(c),
            "ativo_monitorado": c.get("asset") or c.get("ticker") or "",
            "ticker": c.get("ticker") or "",
            "cik_configurado": c.get("cik") or "—",
            "cik_consultado": cik10 or (tl.get("cik_resolved") and "resolvido_via_ticker_map" or "—"),
            "endpoint": "data.sec.gov/submissions/CIK{cik10}.json",
            "status_http": status if status is not None else "",
            "janela_dias": janela,
            "formularios_allowlist_producao": "SET_DE_CARACTERES (bug)",
            "raw_submissions": (cf["raw_submissions"] if source else "requer_rede_ou_fixture"),
            "date_filtered": (cf["date_filtered"] if source else "requer_rede_ou_fixture"),
            "form_filtered_corrigido": (cf["form_filtered"] if source else "requer_rede_ou_fixture"),
            "accepted_producao": tl.get("filings_found", 0),
            "accepted_corrigido": (cf["accepted"] if source else "requer_rede_ou_fixture"),
            "formularios_vistos": ";".join(f"{k}:{v}" for k, v in sorted(forms_seen.items())) if forms_seen else "",
            "motivo_zero": ("filtro_de_formulario_virou_set_de_caracteres"
                            if tl.get("filings_found", 0) == 0 else "n/a"),
            "diagnostico": diagnostico,
            "evidencia": ("telemetria: attempted=%s success=%s http=%s filings=%s"
                          % (tl.get("attempted"), tl.get("success"),
                             tl.get("status_code"), tl.get("filings_found"))),
            "fonte_dados_auditoria": source or "telemetria_run_meta",
        })
        if per_issuer[-1]["accepted_producao"] == 0:
            sem_resultado.append({
                "emissor": name, "pais": c.get("country") or "",
                "tipo_emissor": _classify_issuer_type(c),
                "cik": cik10 or "—",
                "http": status if status is not None else tl.get("status_code"),
                "motivo": "filtro_de_formulario_virou_set_de_caracteres (bug de config: string em vez de lista)",
                "corrigivel": "sim — normalizar formularios_gatilho",
                "recuperavel_na_janela": (("sim" if source and cf["accepted"] > 0 else
                                           ("provavel" if not source else "verificar"))),
            })

    # agrega estágios
    stage_summary = []
    for s in STAGES:
        stage_summary.append({
            "estagio": s,
            "total_allowlist_corrigida": agg_fixed[s],
            "total_allowlist_defeituosa_producao": agg_buggy[s],
        })

    _write_csv(outp / "auditoria_edgar_por_emissor.csv", per_issuer)
    _write_csv(outp / "auditoria_edgar_por_estagio.csv", per_stage_rows)
    _write_csv(outp / "filings_edgar_amostra.csv", amostra)
    _write_csv(outp / "emissores_edgar_sem_resultado.csv", sem_resultado)
    _write_stage_summary(outp / "auditoria_edgar_por_estagio_agregado.csv", stage_summary)

    return {"per_issuer": per_issuer, "per_stage": per_stage_rows,
            "amostra": amostra, "sem_resultado": sem_resultado,
            "stage_summary": stage_summary, "forms_fixed": sorted(forms_fixed),
            "forms_buggy_size": len(forms_buggy), "n_alvos": len(alvos)}


def _classify_issuer_type(c: dict) -> str:
    """Classificação de tipo de emissor para a allowlist de formulários."""
    t = (c.get("edgar_issuer_type") or "").strip()
    if t:
        return t
    country = c.get("country") or ""
    listing = (c.get("listing") or "").lower()
    if country in ("EUA",):
        return "companhia_domestica_eua"
    if country == "Canadá":
        return "mjds_canadense(40-F/6-K)"
    if any(x in listing for x in ("nyse", "nasdaq")) and country not in ("EUA", "Canadá"):
        return "foreign_private_issuer(20-F/6-K)"
    return "a_classificar"


def _write_csv(path: Path, rows: list[dict]):
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    keys = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _write_stage_summary(path: Path, rows: list[dict]):
    _write_csv(path, rows)
