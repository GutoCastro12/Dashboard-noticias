# -*- coding: utf-8 -*-
"""Persistência REAL e única do onboarding histórico de 180 dias para os
quatro emissores peruanos monitorados (Yura, Trupal, Coazucar, Yobel) +
related_entities autorizadas da Coazucar (Casa Grande, Cartavio, San
Jacinto, Sintuco, Agrolmos, Chiquitoy), como contexto relacionado.

Reusa EXCLUSIVAMENTE funções reais e já testadas de risk_dashboard.py, no
mesmo ORDENAMENTO usado pelo laço de produção (main(), risk_dashboard.py):
    translate_articles -> classify_and_attribute -> dedupe_articles ->
    consolidate_with_llm -> resolve_google_news_urls -> merge_into_history ->
    resolve_history_urls -> calibrate_thresholds -> build_evolution ->
    build_feed -> build_changes -> render_html.

Não reimplementa classificação, atribuição, score, deduplicação, ocorrência
ou renderização. Não faz commit. Não faz push. Não dispara workflow. Não faz
backfill dos demais emissores — eles servem só de PROVA DE ISOLAMENTO: nada
neles pode mudar (exceto B3, já corrigida e mesclada ANTES desta branch —
B3 também não pode mudar de novo aqui).

MODOS
-----
  python persist_peru_onboarding_180d.py --check   -> só valida, não escreve nada.
  python persist_peru_onboarding_180d.py --apply   -> exige --check ok, faz backup,
                                                       persiste, testa idempotência,
                                                       regenera HTML, gera relatório.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # todas as funções reais assumem cwd == raiz do projeto
sys.path.insert(0, str(ROOT))

import risk_dashboard as rd  # noqa: E402  (import após chdir/sys.path)

PERU = ["Yura", "Trupal", "Coazucar", "Yobel"]
COAZUCAR_SUBS = ["Casa Grande", "Cartavio", "San Jacinto", "Sintuco",
                  "Agrolmos", "Chiquitoy"]
DAY = 86400
NOW_TS = time.time()

HISTORY_PATH = ROOT / "risk_history.json"
CONFIG_PATH = ROOT / "config_risco.yaml"
DASHBOARD_HTML = ROOT / "dashboard_risco.html"
INDEX_HTML = ROOT / "index.html"
RUN_META_PATH = ROOT / "run_meta.json"
INTL_HIST_PATH = ROOT / "international_search_history.json"

OUT_DIR = ROOT / "out_peru_onboarding_180d"

# Corpus real, já coletado e aprovado no dry-run da Fase 1 (worktree
# separado, somente leitura aqui — nunca escrito). Artigos já vieram do
# Google News real; ainda não passaram por classify_and_attribute (não têm
# "events"). Se o arquivo não existir, seguimos só com fetch ao vivo.
SHADOW_CORPUS = Path(
    "C:/Users/User/OneDrive/files_DashRisk/.claude/worktrees/"
    "agent-a04bcb969e096e8b1/out_peru_watchlist_test/pipeline_integration/"
    "attributed_articles.json"
)

SYNTHETIC_MARKERS = ("exemplo.test", "fixture", "synthetic", "test_case", "cenario_sintetico")
GEO_REJECT_YURA = ("distrito de yura", "carretera de yura", "yura tech", "yura corporation")


def cfg_for_onboarding_window(cfg: dict) -> dict:
    """Cópia RASA de cfg com `dashboard.period` sobrescrito para 180d (só em
    memória, nunca grava em config_risco.yaml). O default de produção
    (`period: 7d`, config_risco.yaml) é certo para execuções recorrentes,
    mas limitaria a busca ao vivo desta persistência única a 7 dias — o
    parâmetro `when:{period}` da query do Google News RSS é o único jeito
    de alcançar 180 dias reais na função `fetch_query_result` já existente
    (não reimplementa busca: só troca o período que ELA usa). Também eleva
    `max_articles_per_query` (default 15) para não truncar uma janela 25x
    maior que a de produção."""
    dash = dict(cfg.get("dashboard", {}))
    dash["period"] = "180d"
    dash["max_articles_per_query"] = max(dash.get("max_articles_per_query", 15), 60)
    out = dict(cfg)
    out["dashboard"] = dash
    return out

# Arquivos que NUNCA podem ser alterados por esta execução (código/config/
# workflows) — hash antes/depois tem que bater.
FROZEN_FILES_TO_HASH = [
    ROOT / "config_risco.yaml",
    ROOT / "risk_dashboard.py",
    ROOT / "semantic_audit.py",
    ROOT / "link_debt_audit.py",
    ROOT / "template_risco.html.j2",
]
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def hash_frozen_set() -> dict:
    out = {str(p.relative_to(ROOT)): sha256_file(p) for p in FROZEN_FILES_TO_HASH}
    if WORKFLOW_DIR.exists():
        for wf in sorted(WORKFLOW_DIR.glob("*.yml")):
            out[str(wf.relative_to(ROOT))] = sha256_file(wf)
    return out


def evolution_by_company(history: dict, cfg: dict, window_days: int | None = None) -> dict:
    """Roda build_evolution (função real) e devolve dict company -> row
    serializável, para comparação de isolamento e para relatório."""
    rows = rd.build_evolution(copy.deepcopy(history), cfg, window_days=window_days)
    return {r["company"]: json.loads(json.dumps(r, default=str, ensure_ascii=False)) for r in rows}


def collect_direct_pool(cfg: dict, watchlist_by_name: dict, session) -> list[dict]:
    """Corpus 'direto' (shadow real reprocessado + fetch ao vivo dos 4
    candidatos). Precisa passar por classify_and_attribute — ainda não tem
    'events'."""
    raw_articles: list[dict] = []
    if SHADOW_CORPUS.exists():
        with open(SHADOW_CORPUS, encoding="utf-8") as f:
            shadow_raw = json.load(f)
        for rec in shadow_raw:
            blob = json.dumps(rec, ensure_ascii=False).lower()
            if any(m in blob for m in SYNTHETIC_MARKERS):
                continue
            url = rec.get("url", "")
            if not url or not url.startswith("http"):
                continue
            pub_ts = rec.get("pub_ts")
            if not pub_ts or (NOW_TS - pub_ts) > 180 * DAY:
                continue
            # descarta campos de classificação/atribuição já presentes no
            # corpus (companies, events_by_company, mention_roles...) —
            # classify_and_attribute é quem decide de novo, com as regras
            # ATUAIS (a atribuição gravada no corpus pode ser de execução
            # anterior). Mantém só os campos "crus" de artigo.
            clean = {k: rec.get(k) for k in
                     ("title", "url", "summary", "source", "domain", "pub_ts",
                      "pub_iso", "language")}
            raw_articles.append(clean)
    else:
        print(f"   aviso: corpus shadow não encontrado em {SHADOW_CORPUS} "
              f"(seguindo só com fetch ao vivo)")

    cfg180 = cfg_for_onboarding_window(cfg)
    live_fetched: list[dict] = []
    for name in PERU:
        comp = watchlist_by_name.get(name)
        if not comp:
            continue
        aliases = comp.get("aliases") or [name]
        termo = aliases[0]
        pais = comp.get("country", "")
        query = f'"{termo}" {pais}'.strip() if pais else f'"{termo}"'
        try:
            loc = rd.locale_for_company(comp, cfg)
        except Exception:
            loc = None
        try:
            arts = rd.fetch_query(query, cfg180, session, locale=loc)
            live_fetched.extend(arts)
            print(f"   fetch ao vivo '{name}': {len(arts)} resultado(s) brutos (janela 180d).")
        except Exception as exc:
            print(f"   aviso: fetch ao vivo '{name}' falhou: {type(exc).__name__}: {exc}")

    def dedup_key(a):
        return a.get("url") or (a.get("title", ""), a.get("pub_ts"))

    seen: dict = {}
    merged_pool: list[dict] = []
    for a in raw_articles + live_fetched:
        k = dedup_key(a)
        if k in seen:
            continue
        seen[k] = a
        merged_pool.append(a)
    return merged_pool


def collect_related_entities_pool(cfg: dict, watchlist_by_name: dict, session) -> list[dict]:
    """Artigos das related_entities autorizadas da Coazucar — já vêm
    TOTALMENTE formatados por fetch_related_entities_context como contexto
    (companies=['Coazucar'], events_by_company vazio, context_events_by_company
    preenchido). NÃO passam por classify_and_attribute (isso sobrescreveria
    a estrutura de contexto)."""
    coazucar_cfg = watchlist_by_name.get("Coazucar")
    if not coazucar_cfg or not coazucar_cfg.get("fetch_related_entities"):
        return []
    cfg180 = cfg_for_onboarding_window(cfg)
    try:
        related_arts = rd.fetch_related_entities_context(coazucar_cfg, cfg180, session=session)
    except Exception as exc:
        print(f"   aviso: related_entities Coazucar falhou: {exc}")
        return []
    real_related = [a for a in related_arts if not a.get("_coverage_only")]
    # janela de 180 dias também para o contexto de subsidiárias
    in_window = [a for a in real_related
                 if a.get("pub_ts") and (NOW_TS - a["pub_ts"]) <= 180 * DAY]
    seen_urls: set[str] = set()
    out = []
    for a in in_window:
        u = a.get("url", "")
        if u in seen_urls:
            continue
        seen_urls.add(u)
        out.append(a)
    subs_found = sorted({a.get("query_related_entity", "") for a in out})
    print(f"   related_entities Coazucar: {len(out)} artigo(s) de contexto "
          f"dentro da janela de 180d (subsidiárias com achado: {subs_found}).")
    return out


def classify_direct_pool(direct_pool: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """Roda o MESMO trecho do laço de produção sobre o corpus direto:
    translate_articles -> classify_and_attribute -> filtro geográfico Yura ->
    filtro de escopo (só PERU) -> dedupe_articles -> consolidate_with_llm
    (fail-open sem GEMINI_API_KEY, deduplicação textual sempre ativa)."""
    rd.translate_articles(direct_pool, cfg)

    accepted: list[dict] = []
    rejected: list[dict] = []
    for a in direct_pool:
        titulo = a.get("title", "")
        tl = titulo.lower()
        if any(g in tl for g in GEO_REJECT_YURA) and "yura" in tl:
            rejected.append({"title": titulo, "url": a.get("url", ""),
                              "reason": "geografico_ou_homonimo_estrangeiro_yura"})
            continue
        try:
            rd.classify_and_attribute(a, cfg)
        except Exception as exc:
            rejected.append({"title": titulo, "url": a.get("url", ""),
                              "reason": f"erro_classificacao:{exc}"})
            continue
        if not a.get("events") or not a.get("companies"):
            rejected.append({"title": titulo, "url": a.get("url", ""),
                              "reason": "sem_evento_ou_sem_emissor_apos_classificacao"})
            continue
        companies = a.get("companies") or []
        out_of_scope = [c for c in companies if c not in PERU and c != rd.MARKET_LABEL]
        if out_of_scope:
            rejected.append({"title": titulo, "url": a.get("url", ""),
                              "reason": f"empresa_fora_do_escopo_autorizado:{out_of_scope}"})
            continue
        if not any(c in PERU for c in companies):
            rejected.append({"title": titulo, "url": a.get("url", ""),
                              "reason": "nao_atribuido_a_nenhum_dos_4_candidatos"})
            continue
        accepted.append(a)

    return accepted, rejected


def apply_dedup_and_consolidation(accepted: list[dict], cfg: dict, history: dict) -> list[dict]:
    """dedupe_articles (textual, contra o lote e contra o histórico) +
    consolidate_with_llm (fail-open sem chave — mantém keywords)."""
    before = len(accepted)
    deduped = rd.dedupe_articles(accepted, history, cfg)
    print(f"   dedupe_articles: {before} -> {len(deduped)} "
          f"({before - len(deduped)} duplicata(s) textual(is) rejeitada(s)).")
    consolidated = rd.consolidate_with_llm(deduped, cfg, history)
    return consolidated


def validate_final_pool(pool: list[dict]) -> list[str]:
    """Validações obrigatórias antes de qualquer escrita. Retorna lista de
    problemas (vazia = tudo OK)."""
    problems: list[str] = []
    seen_urls: set[str] = set()
    allowed_companies = set(PERU)
    for a in pool:
        url = a.get("url", "")
        blob = json.dumps(a, ensure_ascii=False, default=str).lower()
        if any(m in blob for m in SYNTHETIC_MARKERS):
            problems.append(f"artigo sintético detectado: {a.get('title','')[:80]!r}")
        if not url or not url.startswith("http"):
            problems.append(f"URL ausente/inválida: {a.get('title','')[:80]!r}")
        if "exemplo.test" in url:
            problems.append(f"domínio exemplo.test: {url}")
        if url in seen_urls:
            problems.append(f"URL duplicada dentro do lote aceito: {url}")
        seen_urls.add(url)
        pub_ts = a.get("pub_ts")
        if not pub_ts or (NOW_TS - pub_ts) > 180 * DAY:
            problems.append(f"fora da janela de 180 dias: {a.get('title','')[:80]!r}")
        companies = a.get("companies") or []
        ctx = a.get("context_events_by_company") or {}
        for c in companies:
            if c not in allowed_companies and c != rd.MARKET_LABEL:
                problems.append(f"empresa fora do escopo autorizado nas companies: {c!r} "
                                 f"({a.get('title','')[:60]!r})")
        for c in ctx.keys():
            if c not in allowed_companies:
                problems.append(f"empresa fora do escopo autorizado em context_events_by_company: {c!r}")
        # subsidiárias da Coazucar só podem entrar como CONTEXTO (não score)
        for c, evs in ctx.items():
            if c == "Coazucar":
                for e in evs:
                    if e.get("scoreable"):
                        problems.append("evento de subsidiária marcado scoreable=True "
                                        f"(deveria ser sempre False): {e}")
        ebc = a.get("events_by_company") or {}
        if "Coazucar" in ebc and ebc["Coazucar"] and a.get("query_scope") == "related_entity":
            problems.append("artigo de related_entity com events_by_company['Coazucar'] "
                             f"não-vazio (transferiria score da subsidiária): {url}")
    return problems


def run_check(cfg: dict, watchlist_by_name: dict) -> tuple[bool, list[dict], dict]:
    print(" Coletando e classificando artigos reais...")
    session = rd.requests.Session()
    history_before = rd.load_history(HISTORY_PATH)

    direct_pool = collect_direct_pool(cfg, watchlist_by_name, session)
    accepted_direct, rejected_direct = classify_direct_pool(direct_pool, cfg)
    accepted_direct = apply_dedup_and_consolidation(accepted_direct, cfg, history_before)

    related_pool = collect_related_entities_pool(cfg, watchlist_by_name, session)

    final_pool = accepted_direct + related_pool
    rejected = rejected_direct

    print(f"   aceitos (diretos): {len(accepted_direct)} | "
          f"aceitos (contexto subsidiárias): {len(related_pool)} | "
          f"rejeitados: {len(rejected)}")

    problems = validate_final_pool(final_pool)
    if problems:
        print("\n VALIDAÇÃO FALHOU:")
        for p in problems:
            print(f"   - {p}")
        return False, final_pool, {"ok": False, "problems": problems}

    evo_before_all = evolution_by_company(history_before, cfg)
    hist_dryrun = copy.deepcopy(history_before)
    keep_days = cfg["dashboard"].get("history_keep_days", 400)
    merged_ids = rd.merge_into_history(hist_dryrun, final_pool, keep_days=keep_days)
    evo_after_all = evolution_by_company(hist_dryrun, cfg)

    non_peru_changed = []
    for company, row_before in evo_before_all.items():
        if company in PERU:
            continue
        row_after = evo_after_all.get(company)
        if row_after != row_before:
            non_peru_changed.append(company)

    print(f"\n Pré-visualização da persistência:")
    print(f"   artigos que seriam mesclados: {len(merged_ids)}")
    for c in PERU:
        b = (evo_before_all.get(c) or {}).get("total_score")
        a = (evo_after_all.get(c) or {}).get("total_score")
        print(f"   {c}: score {b} -> {a}")
    if non_peru_changed:
        print(f"\n ABORTADO: {len(non_peru_changed)} emissor(es) fora do escopo mudariam: "
              f"{non_peru_changed}")
        return False, final_pool, {"ok": False, "non_peru_changed": non_peru_changed}

    print(f"\n Isolamento confirmado: nenhum emissor fora de {PERU} seria alterado.")
    print(" --check concluído com sucesso. Nada foi escrito.")
    return True, final_pool, {
        "ok": True,
        "merged_ids": merged_ids,
        "rejected": rejected,
        "score_before": {c: (evo_before_all.get(c) or {}).get("total_score") for c in PERU},
        "score_after": {c: (evo_after_all.get(c) or {}).get("total_score") for c in PERU},
    }


def make_backup() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = ROOT / f"backup_peru_onboarding_180d_{ts}"
    backup_dir.mkdir(parents=False, exist_ok=False)
    for p in [HISTORY_PATH, DASHBOARD_HTML, INDEX_HTML, RUN_META_PATH, INTL_HIST_PATH]:
        if p.exists():
            shutil.copy2(p, backup_dir / p.name)
        else:
            print(f"   aviso: {p.name} não existe, sem backup necessário.")
    print(f" Backup criado em: {backup_dir}")
    return backup_dir


def restore_backup(backup_dir: Path) -> None:
    print(f" Restaurando backup de {backup_dir} ...")
    for name in ["risk_history.json", "dashboard_risco.html", "index.html",
                 "run_meta.json", "international_search_history.json"]:
        src = backup_dir / name
        if src.exists():
            shutil.copy2(src, ROOT / name)
    print(" Backup restaurado. Nenhuma alteração persistente permanece.")


def run_apply(cfg: dict, watchlist_by_name: dict) -> int:
    ok, final_pool, check_meta = run_check(cfg, watchlist_by_name)
    if not ok:
        print("\n --apply abortado: --check não passou.")
        return 1

    frozen_before = hash_frozen_set()
    backup_dir = make_backup()

    try:
        history = rd.load_history(HISTORY_PATH)
        articles_before_count = len(history.get("articles", {}))
        keep_days = cfg["dashboard"].get("history_keep_days", 400)
        merged_ids = rd.merge_into_history(history, final_pool, keep_days=keep_days)
        # mesmo passo do laço de produção: resolve redirects do Google News
        # ANTES de mesclar (aqui já mesclamos acima, então corrige o histórico
        # inteiro do mesmo jeito que a execução normal faz para registros
        # antigos ainda não resolvidos).
        rd.resolve_history_urls(history, cfg)

        prev_run = history.get("last_run") or {}
        prev_scores = {c: v.get("score") for c, v in (prev_run.get("status") or {}).items()}
        thresholds = rd.calibrate_thresholds(history, cfg)
        windows = cfg["dashboard"].get("windows", [7, 30, 90, 365])
        data_by_window = {}
        for w in windows:
            data_by_window[str(w)] = {
                "evolution": rd.build_evolution(history, cfg, window_days=w,
                                                 thresholds=thresholds, prev_scores=prev_scores),
                "feed": rd.build_feed(history, cfg, window_days=w),
            }
        default_w = str(cfg["dashboard"].get("default_window", 7))
        evo_ref = data_by_window.get("90", data_by_window[default_w])["evolution"]
        changes = rd.build_changes(history, cfg, merged_ids, prev_run, evo_ref)
        history["last_run"] = {
            "ts": int(datetime.now(timezone.utc).timestamp()),
            "iso": rd.fmt_date_br(rd.get_brt_now()),
            "status": {r["company"]: {"status": r["status"], "score": r["total_score"]}
                       for r in evo_ref},
        }
        rd.save_history(HISTORY_PATH, history)
        print(f" Histórico salvo: {HISTORY_PATH} ({len(history['articles'])} registros, "
              f"+{len(history['articles']) - articles_before_count} novos)")

        html = rd.render_html(data_by_window, cfg, demo=False, changes=changes,
                               payload_thresholds=thresholds)
        DASHBOARD_HTML.write_text(html, encoding="utf-8")
        shutil.copy2(DASHBOARD_HTML, INDEX_HTML)  # mesmo passo do workflow: cp dashboard_risco.html index.html
        print(f" HTML regenerado: {DASHBOARD_HTML.name} e {INDEX_HTML.name}")

        rm = {}
        if RUN_META_PATH.exists():
            rm = json.loads(RUN_META_PATH.read_text(encoding="utf-8"))
        rm["peru_onboarding_180d"] = {
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "companies": PERU,
            "related_entities_coazucar": [
                r.get("entity_name")
                for r in (watchlist_by_name.get("Coazucar", {}).get("related_entities") or [])
            ],
            "lookback_days": 180,
            "articles_merged": len(merged_ids),
        }
        rm["run_count"] = history.get("run_count", rm.get("run_count", 0))
        RUN_META_PATH.write_text(json.dumps(rm, ensure_ascii=False, indent=1), encoding="utf-8")
        print(" run_meta.json atualizado (só campos do onboarding + run_count).")

        # --- idempotência: reprocessar os MESMOS artigos contra o histórico
        # já persistido; nada deve mudar de novo.
        history_reloaded = rd.load_history(HISTORY_PATH)
        evo_before_idem = evolution_by_company(history_reloaded, cfg)
        history_idem_copy = copy.deepcopy(history_reloaded)
        merged_ids_2 = rd.merge_into_history(history_idem_copy, copy.deepcopy(final_pool),
                                              keep_days=keep_days)
        evo_after_idem = evolution_by_company(history_idem_copy, cfg)
        idem_ok = (len(merged_ids_2) == 0) and (evo_before_idem == evo_after_idem)

        frozen_after = hash_frozen_set()
        frozen_ok = frozen_before == frozen_after

        if not idem_ok or not frozen_ok:
            print("\n IDEMPOTÊNCIA OU ISOLAMENTO FALHOU — restaurando backup automaticamente.")
            if not idem_ok:
                print(f"   segunda mescla não-vazia: {len(merged_ids_2)} artigo(s) adicional(is)")
            if not frozen_ok:
                changed = [k for k in frozen_before if frozen_before[k] != frozen_after.get(k)]
                print(f"   arquivos protegidos alterados: {changed}")
            restore_backup(backup_dir)
            return 1

        print(" Idempotência confirmada: 2a execução -> 0 novos registros, 0 mudança de score.")
        print(" Isolamento de config/código/workflows confirmado (hashes idênticos).")

        evo_final = {c: (evo_after_idem.get(c) or {}).get("total_score") for c in PERU}
        report = {
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "companies": PERU,
            "articles_merged": merged_ids,
            "articles_merged_count": len(merged_ids),
            "rejected": check_meta.get("rejected"),
            "score_before": check_meta.get("score_before"),
            "score_after": evo_final,
            "idempotency_ok": idem_ok,
            "isolation_ok": frozen_ok,
            "backup_dir": str(backup_dir),
            "files_changed": ["risk_history.json", "dashboard_risco.html", "index.html",
                               "run_meta.json"],
        }
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / "persistence_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"\n Relatório final: {OUT_DIR / 'persistence_report.json'}")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    except Exception as exc:
        print(f"\n ERRO durante a persistência: {type(exc).__name__}: {exc}")
        print(" Restaurando backup por segurança...")
        restore_backup(backup_dir)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="Só valida, não escreve nada.")
    g.add_argument("--apply", action="store_true", help="Persiste de verdade (após --check ok).")
    args = ap.parse_args()

    cfg = rd.load_config(str(CONFIG_PATH))
    watchlist_by_name = {c["name"]: c for c in cfg.get("watchlist", [])}
    for name in PERU:
        if name not in watchlist_by_name:
            print(f" ERRO: {name!r} não está em config_risco.yaml — abortando.")
            return 1

    if args.check:
        ok, _final_pool, _meta = run_check(cfg, watchlist_by_name)
        return 0 if ok else 1
    else:
        return run_apply(cfg, watchlist_by_name)


if __name__ == "__main__":
    sys.exit(main())
