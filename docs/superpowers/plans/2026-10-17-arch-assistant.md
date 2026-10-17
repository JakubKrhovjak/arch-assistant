# Arch Assistant – plán implementace

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tray aplikace pro Arch + Plasma, která po přihlášení spustí kontrolní skripty, při nálezu pošle notifikaci s tlačítky Ano / Ano příště automaticky / Ne, provede schválené akce a vždy ohlásí výsledek; Claude (Haiku 5.5) jen shrnuje.

**Architecture:** Python balíček `arch_assistant` (jádro: konfigurace, běh kontrol, stav a deduplikace, akce, shrnutí, notifikace, tray) + adresáře `checks/` a `actions/` se samostatnými skripty s JSON kontraktem. Kontroly běží paralelně v `ThreadPoolExecutor`, GUI je PySide6, notifikace jdou přes D-Bus `org.freedesktop.Notifications`. Bez API klíče se shrnutí skládá ze šablon.

**Tech Stack:** Python 3.13 (systémový), PySide6 6.12 (systémový balíček), `anthropic` SDK + `pydantic` (venv `~/.local/share/arch-assistant/venv` se `--system-site-packages`, aby viděl PySide6), bash skripty, `pytest`, `shellcheck`.

**Spec:** `docs/superpowers/specs/2026-10-17-arch-assistant-design.md`

## Global Constraints

- Jazyk UI, notifikací, názvů akcí, komentářů, README a výstupů skriptů: **angličtina** (rozhodnutí uživatele 2026-10-17, přebíjí české texty uvedené dál v plánu – kde plán cituje český text, použij anglický ekvivalent; testy kontrolují anglické řetězce). Shrnutí od Claude také anglicky.
- Model výchozí `claude-haiku-5-5`, volitelně `claude-sonnet-5-5`; `max_tokens` 1024; `output_config={"effort": "low"}`; **jedno volání na běh, jen když existuje nový nález**; bez klíče šablony.
- Klíč API jen z `api_key_file` (výchozí `~/.config/zsh/secrets.zsh`, řádek `ANTHROPIC_API_KEY=…`); nikdy do konfigurace, stavu ani logu.
- Root přes existující `sudo` bez hesla; všechny příkazy pod `sudo` s absolutní cestou a pevnými argumenty.
- Nikdy automaticky: `remove-package`, `pacdiff`, system jednotky v `restart-unit`, cokoli z `docker-vm` (ta nemá akci).
- `update` v režimu `auto` se přepne na dotaz, když `arch-news` hlásí ruční zásah pro čekající balíček, nebo když dávka obsahuje `qemu*`/`libvirt*` a běží nějaká VM.
- Režimy akcí: `ask` / `auto` / `never`; výchozí všude `ask`.
- Kontrola: timeout 60 s, pád = nález `severity: "low"`, titulek `Kontrola <id> selhala`. Akce: timeout 1800 s pro `update`, jinak 300 s.
- Cesty: konfigurace `~/.config/arch-assistant/config.toml` a `actions.toml`; stav `~/.local/state/arch-assistant/state.json`; log `~/.local/state/arch-assistant/log/`.
- Pravidlo hlášení `updates`: nová notifikace jen při zásadním balíčku, nebo ≥ 20 balíčků, nebo ≥ 7 dní od posledního hlášení. Ostatní nálezy: změna otisku nebo 7 dní.
- ESP je `/boot/efi` (práh 30 MB), ostatní svazky práh 10 %.
- Kontroly běží s `nice -n 10` a `ionice -c3`.

## Review Focus

1. Kontrola, která vypíše nevalidní JSON nebo nic (např. `checkupdates` vrátí kód 2 při zamčené DB): runner ji musí převést na nález „kontrola selhala“, ne spadnout. Test v Task 2.
2. Souběh dvou cyklů (uživatel klikne „Zkontrolovat teď“ během běžící akce `update`): druhý cyklus se nesmí rozběhnout, dokud akce neskončí. Test v Task 8 (zámek `cycle_running`).
3. Notifikace s tlačítky, na kterou uživatel neklikne a Plasma ji zavře (signál `NotificationClosed`): akce zůstane v nabídce jako čekající, nic se nespustí. Test v Task 7.
4. `secrets.zsh` s klíčem v uvozovkách, s `export`, nebo bez souboru vůbec: čtečka vrátí klíč nebo `None`, nikdy výjimku. Test v Task 6.
5. Akce `update`, při níž pacman skončí chybou uprostřed (konflikt souborů): výsledek `ok=False`, popup s prvním řádkem chyby, po akci se přesto spustí `secure-boot` a `reboot-needed`. Test v Task 5.

---

## Struktura souborů

```
arch-assistant/
  pyproject.toml                 # balíček arch_assistant, závislosti anthropic, pydantic; dev: pytest
  bin/arch-assistant             # spouštěč: venv python -m arch_assistant "$@"
  arch_assistant/
    __init__.py
    __main__.py                  # CLI: (bez argumentu) tray | check [ID…] | act ID [ARGS…] | once
    model.py                     # Finding, Action, ActionResult, Summary (dataclasses) + parsování JSON kontraktu
    config.py                    # Config, load_config, action modes (actions.toml), check_env
    checks.py                    # discover_checks, run_check, run_all
    state.py                     # State: otisky, hlášení, odmítnutí, baseline; should_report
    actions.py                   # run_action, needs_confirmation, POST_HOOKS
    secrets.py                   # read_api_key
    summarizer.py                # template_summary, claude_summary, summarize
    notify.py                    # Notifier (QtDBus), result_popup
    tray.py                      # TrayApp: ikona, nabídka, časovač, cyklus, fronta čekajících akcí
    log.py                       # logging do ~/.local/state/arch-assistant/log/assistant.log (rotace 5×1 MB)
  checks/<id>.sh | <id>.py       # jedna kontrola = jeden soubor, název = id
  actions/<id>.sh
  assets/tray.svg tray-attention.svg tray-busy.svg
  install.sh
  tests/
    conftest.py                  # fixture `shim`: dočasný bin adresář s falešnými příkazy, PATH, env AA_*
    test_*.py
```

Každý úkol končí commitem. Commity dělá implementátor (repo je nové, bez pravidla „commituje uživatel“).

---

### Task 1: Kostra balíčku, model a konfigurace

**Files:**
- Create: `pyproject.toml`, `arch_assistant/__init__.py`, `arch_assistant/model.py`, `arch_assistant/config.py`, `arch_assistant/log.py`, `tests/test_model.py`, `tests/test_config.py`, `.gitignore`

**Interfaces:**
- Produces (`model.py`):
  - `Severity = Literal["none", "low", "medium", "high", "critical"]`
  - `@dataclass(frozen=True) Action(id: str, label: str, args: tuple[str, ...] = ())`
  - `@dataclass(frozen=True) Finding(id: str, severity: Severity, title: str, summary: str, items: tuple[dict, ...] = (), actions: tuple[Action, ...] = (), error: str | None = None)`
  - `Finding.from_json(text: str) -> Finding` (vyhodí `ValueError` při chybějícím `id`/`severity`/`title` nebo neznámé severity)
  - `Finding.failed(check_id: str, error: str) -> Finding` (severity `low`, title `Kontrola <id> selhala`, summary = první řádek `error`)
  - `Finding.is_empty -> bool` (severity `none`)
  - `@dataclass ActionResult(action_id: str, ok: bool, output: str, detail: str, duration_s: float)`
  - `@dataclass Summary(headline: str, body: str, risks: list[str])`
- Produces (`config.py`):
  - `Mode = Literal["ask", "auto", "never"]`
  - `@dataclass Config(model: str, interval_hours: float, api_key_file: Path, critical_packages: list[str], thresholds: dict[str, float], actions: dict[str, Mode], config_dir: Path, state_dir: Path, checks_dir: Path, actions_dir: Path)`
  - `DEFAULTS: dict` – `model="claude-haiku-5-5"`, `interval_hours=6`, `critical_packages=["linux", "linux-lts", "linux-firmware", "systemd", "plasma-*", "kwin", "mesa", "qemu*", "libvirt*", "docker", "k3s"]`, `thresholds={"disk_pct": 10, "esp_mb": 30, "pkg_cache_gb": 2, "user_cache_gb": 1, "journal_err": 50, "unused_days": 90, "hog_cpu_pct": 50, "hog_rss_gb": 4, "hog_minutes": 5, "mirror_days": 7, "update_min_count": 20, "report_days": 7}`
  - `load_config(config_dir: Path | None = None, state_dir: Path | None = None) -> Config` – čte `config.toml` (tomllib) přes DEFAULTS, pak `actions.toml`; chybějící soubory = výchozí hodnoty; `checks_dir`/`actions_dir` = adresáře `checks/` a `actions/` vedle balíčku (`Path(__file__).parent.parent`)
  - `set_action_mode(config_dir: Path, action_id: str, mode: Mode) -> None` – přepíše `actions.toml` (řádky `id = "mode"`, bez knihovny)
  - `check_env(cfg: Config) -> dict[str, str]` – `AA_CRITICAL_PACKAGES` (mezerami), `AA_DISK_PCT`, `AA_ESP_MB`, `AA_PKG_CACHE_GB`, `AA_USER_CACHE_GB`, `AA_JOURNAL_ERR`, `AA_UNUSED_DAYS`, `AA_HOG_CPU_PCT`, `AA_HOG_RSS_GB`, `AA_HOG_MINUTES`, `AA_MIRROR_DAYS`, `AA_STATE_DIR`
- Produces (`log.py`): `get_logger(state_dir: Path) -> logging.Logger` (RotatingFileHandler `log/assistant.log`, 1 MB × 5).

- [ ] **Step 1: Založit `pyproject.toml`** – `[project] name = "arch-assistant"`, `requires-python = ">=3.13"`, `dependencies = ["anthropic>=1.0", "pydantic>=2"]`, `[project.optional-dependencies] dev = ["pytest"]`, `[tool.pytest.ini_options] testpaths = ["tests"]`. `.gitignore`: `__pycache__/`, `.pytest_cache/`, `*.egg-info/`.

- [ ] **Step 2: Napsat padající testy `tests/test_model.py`**

```python
def test_from_json_parses_contract():
    f = Finding.from_json('{"id":"x","severity":"low","title":"T","summary":"S","items":[{"name":"a","size_mb":1}],"actions":[{"id":"clean","label":"Smazat","args":["a"]}]}')
    assert f.actions[0] == Action("clean", "Smazat", ("a",))
    assert f.items[0]["size_mb"] == 1

def test_from_json_rejects_unknown_severity():
    with pytest.raises(ValueError): Finding.from_json('{"id":"x","severity":"huge","title":"T"}')

def test_failed_finding():
    f = Finding.failed("updates", "boom\nmore")
    assert (f.severity, f.title, f.summary, f.error) == ("low", "Kontrola updates selhala", "boom", "boom\nmore")

def test_is_empty():
    assert Finding("x", "none", "", "").is_empty
```

- [ ] **Step 3: Napsat padající testy `tests/test_config.py`** – `test_defaults_without_files` (tmp dir bez souborů → `cfg.model == "claude-haiku-5-5"`, `cfg.actions == {}`, `cfg.thresholds["esp_mb"] == 30`); `test_config_toml_overrides_model` (zapsat `model = "claude-sonnet-5-5"` → načte); `test_set_action_mode_roundtrip` (`set_action_mode(d, "update", "auto")` dvakrát s jiným id → `load_config(d).actions == {"update": "auto", "orphans": "never"}`); `test_check_env_contains_thresholds` (`check_env(cfg)["AA_ESP_MB"] == "30"`, `"linux"` v `AA_CRITICAL_PACKAGES`).

- [ ] **Step 4: Spustit testy, ověřit pád** – Run: `python -m pytest -q`. Expected: ImportError / FAIL.

- [ ] **Step 5: Implementovat `model.py`, `config.py`, `log.py`** podle Interfaces.

- [ ] **Step 6: Spustit testy** – Run: `python -m pytest -q`. Expected: všechny PASS.

- [ ] **Step 7: Commit** – `git add -A && git commit -m "feat: kostra balíčku, model nálezů a konfigurace"`

---

### Task 2: Běh kontrol

**Files:**
- Create: `arch_assistant/checks.py`, `tests/conftest.py`, `tests/test_checks.py`

**Interfaces:**
- Consumes: `Finding`, `Config`, `check_env`.
- Produces (`checks.py`):
  - `discover_checks(checks_dir: Path, only: Iterable[str] | None = None) -> list[Path]` – soubory `*.sh` a `*.py` seřazené podle jména; `only` filtruje podle stem.
  - `run_check(script: Path, env: dict[str, str], timeout: float = 60) -> Finding` – spustí `nice -n 10 ionice -c3 <script>` (`.py` přes `sys.executable`), env = `os.environ | env`; stdout → `Finding.from_json`; nenulový kód, timeout, prázdný stdout nebo `ValueError` → `Finding.failed(stem, <stderr nebo popis>)`.
  - `run_all(checks_dir: Path, env: dict[str, str], only=None, timeout=60, max_workers=8) -> list[Finding]` – `ThreadPoolExecutor`, pořadí výsledků = pořadí skriptů.
- Produces (`tests/conftest.py`): fixture `shim(tmp_path, monkeypatch)` vracející objekt s metodou `cmd(name: str, stdout: str = "", exit_code: int = 0, stderr: str = "")`, která zapíše spustitelný skript do `tmp_path/bin` a jednou prependne ten adresář do `PATH`; a fixture `check_runner` s metodou `run(check_id: str, env: dict[str, str] = {}) -> Finding`, která spustí `checks/<id>.*` z repa přes `run_check` s env `AA_*` z `check_env(load_config(tmp_path, tmp_path))` + override.

- [ ] **Step 1: Napsat padající testy `tests/test_checks.py`** – vytvoří dočasné skripty v `tmp_path/checks`:
  - `test_run_check_parses_output`: skript `echo '{"id":"ok","severity":"low","title":"T","summary":"S"}'` → `Finding.id == "ok"`.
  - `test_run_check_invalid_json_is_failed_finding`: skript `echo nope` → `title == "Kontrola bad selhala"`, `error` obsahuje `nope`.
  - `test_run_check_nonzero_exit_uses_stderr`: `echo err >&2; exit 2` → `summary == "err"`.
  - `test_run_check_timeout`: `sleep 5` s `timeout=0.2` → `error` obsahuje `timeout`.
  - `test_run_all_keeps_order_and_runs_parallel`: tři skripty, každý `sleep 0.3`; celkem pod 0.7 s; ids v pořadí jmen.
  - `test_discover_only`: `only=["b"]` vrátí jen `b.sh`.

- [ ] **Step 2: Spustit, ověřit pád** – Run: `python -m pytest tests/test_checks.py -q`. Expected: FAIL (ImportError).

- [ ] **Step 3: Implementovat `checks.py` a `conftest.py`.**

- [ ] **Step 4: Spustit testy** – Run: `python -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit** – `git commit -am "feat: paralelní běh kontrol s JSON kontraktem"` (po `git add`).

---

### Task 3: Stav a deduplikace

**Files:**
- Create: `arch_assistant/state.py`, `tests/test_state.py`

**Interfaces:**
- Consumes: `Finding`, `Config`.
- Produces:
  - `fingerprint(f: Finding) -> str` – sha256 z `id`, `title`, `summary`, seřazených `items[*].name` (nebo celé položky bez klíče `size_mb`), prvních 16 hex.
  - `class State(path: Path)` s `load()`/`save()` (JSON, atomický zápis přes tmp + rename), `data: dict` s klíči `reported: {id: {fp, at}}`, `declined: {id: fp}`, `baseline: {}` (volné pro kontroly), `last_update_report: {at, count, critical: []} | None`, `last_update_run: str | None`.
  - `State.should_report(f: Finding, cfg: Config, now: datetime) -> bool`:
    - `f.is_empty` → False; `id == "updates"` → pravidlo z Global Constraints (`critical` = průnik `items[*].name` se vzory `cfg.critical_packages` přes `fnmatch`, nové oproti `last_update_report.critical`; `count = len(items) >= thresholds["update_min_count"]`; nebo `now - at >= report_days`); jinak: otisk ≠ `reported[id].fp` a otisk ≠ `declined[id]`, nebo `now - reported[id].at >= report_days`.
  - `State.mark_reported(f, now)`, `State.mark_declined(f)`, `State.mark_update_run(now)`.
  - `State.new_findings(findings, cfg, now) -> list[Finding]` – filtr přes `should_report`, prázdné vynechá.

- [ ] **Step 1: Napsat padající testy `tests/test_state.py`**:
  - `test_report_first_time_then_not_again`, `test_report_again_after_7_days`, `test_declined_same_fingerprint_not_reported`, `test_changed_fingerprint_reported_even_if_declined`,
  - `test_updates_not_reported_below_20_without_critical` (12 obyčejných balíčků → False),
  - `test_updates_reported_when_critical_appears` (items obsahují `linux` → True; po `mark_reported` s týmiž → False; přidá se `plasma-desktop` → True),
  - `test_updates_reported_at_20`,
  - `test_save_and_load_roundtrip`.

- [ ] **Step 2: Spustit, ověřit pád.** Run: `python -m pytest tests/test_state.py -q`. Expected: FAIL.

- [ ] **Step 3: Implementovat `state.py`.**

- [ ] **Step 4: Spustit testy.** Expected: PASS.

- [ ] **Step 5: Commit** – `git commit -m "feat: stav a deduplikace nálezů"`.

---

### Task 4: Kontroly `updates` a `arch-news`

**Files:**
- Create: `checks/updates.sh`, `checks/arch-news.py`, `tests/test_check_updates.py`, `tests/test_check_arch_news.py`

**Interfaces:**
- Consumes: fixtures `shim`, `check_runner`; env `AA_CRITICAL_PACKAGES`, `AA_STATE_DIR`.
- Produces: nález `updates` – `items = [{"name", "old", "new", "source": "repo"|"aur", "critical": bool}]`, `summary` = „Zásadní: linux 7.2.10 → 7.2.11, plasma-desktop …; dalších N balíčků“ (bez zásadních: „N balíčků“), `severity` = `high` je-li zásadní, jinak `low`; `actions = [{"id":"update","label":"Aktualizovat systém","args":[]}]`; `severity: none` bez aktualizací. Nález `arch-news` – `items = [{"title", "date", "link", "packages": [...]}]` jen položky z posledních 30 dnů, jejichž titulek nebo text obsahuje „manual intervention“ nebo „requires manual“; `summary` = titulky; bez akce; `severity: high`. Feed se cachuje do `$AA_STATE_DIR/arch-news.xml` na 6 h (kontrola nesmí viset bez sítě: timeout 10 s, při chybě použít cache, bez cache `severity: none`).

- [ ] **Step 1: Napsat padající test `tests/test_check_updates.py`** přes `shim`: `checkupdates` → `"linux 7.2.10-arch1-1 -> 7.2.11-arch1-1\nvim 9.1-1 -> 9.2-1"`, `yay` → `"aur/mbpoll-git 1.5-1 -> 1.6-1"` (pro `-Qua`); asserty: `severity == "high"`, dva repo + jeden aur item, `items[0]["critical"] is True`, `items[2]["source"] == "aur"`, `summary.startswith("Zásadní: linux")`, `actions[0].id == "update"`. Druhý test: `checkupdates` kód 2 bez výstupu (nic k aktualizaci) a `yay` prázdné → `severity == "none"`. Třetí: `checkupdates` kód 1 se stderr „ERROR: Cannot fetch updates“ → nález selhal (kód 1 = chyba, kód 2 = nic).

- [ ] **Step 2: Napsat padající test `tests/test_check_arch_news.py`** – test nevolá síť: zapíše do `AA_STATE_DIR/arch-news.xml` RSS se dvěma položkami (jedna „… requires manual intervention“ s datem před 3 dny, druhá obyčejná) s čerstvým mtime a spustí kontrolu s env `AA_NEWS_OFFLINE=1` (kontrola při této proměnné nestahuje). Asserty: jedna položka, `severity == "high"`. Druhý test: položka stará 60 dní → `none`.

- [ ] **Step 3: Spustit, ověřit pád.** Expected: FAIL (skripty neexistují).

- [ ] **Step 4: Implementovat `checks/updates.sh`** (bash, `set -euo pipefail`, JSON skládat přes `jq -n` nebo `python3 -c` – zvolit `python3`, protože `jq` nemusí být nainstalované; zásadní = `fnmatch` přes vzory z `AA_CRITICAL_PACKAGES`) a **`checks/arch-news.py`** (stdlib `urllib`, `xml.etree`, `email.utils.parsedate_to_datetime`; URL `https://archlinux.org/feeds/news/`).

- [ ] **Step 5: Spustit testy + shellcheck.** Run: `python -m pytest -q && shellcheck checks/*.sh`. Expected: PASS, bez nálezů shellchecku.

- [ ] **Step 6: Ruční ověření na stroji.** Run: `python -m arch_assistant check updates arch-news` (CLI vznikne v Task 8; do té doby `python -c "from arch_assistant.checks import *; ..."` nebo přímo `AA_CRITICAL_PACKAGES='linux plasma-*' AA_STATE_DIR=/tmp/aa checks/updates.sh`). Expected: validní JSON.

- [ ] **Step 7: Commit** – `git commit -m "feat: kontroly updates a arch-news"`.

---

### Task 5: Akce a `update`

**Files:**
- Create: `arch_assistant/actions.py`, `actions/update.sh`, `tests/test_actions.py`, `tests/test_action_update.py`

**Interfaces:**
- Consumes: `Action`, `ActionResult`, `Finding`, `Config`.
- Produces (`actions.py`):
  - `TIMEOUTS = {"update": 1800}`; `DEFAULT_TIMEOUT = 300`
  - `POST_HOOKS = {"update": ("secure-boot", "reboot-needed")}` – id kontrol, které tray spustí po akci (Task 8).
  - `run_action(actions_dir: Path, action: Action, log: logging.Logger) -> ActionResult` – spustí `actions/<id>.sh args…`; `output` = stdout (max 3 řádky, zbytek do detail), `detail` = stderr; `ok` = kód 0; timeout → `ok=False`, `output = "Akce <id> překročila čas <n> min"`.
  - `needs_confirmation(action: Action, finding: Finding, findings: list[Finding], running_vms: Callable[[], list[str]]) -> str | None` – vrátí důvod (čeština) nebo `None`: pro `update` – `arch-news` nález s `items[*].packages` protínajícími jména v `finding.items`, nebo položka se jménem `qemu*`/`libvirt*` a `running_vms()` neprázdný; ostatní akce `None`.
  - `NEVER_AUTO = {"remove-package", "pacdiff"}`; `effective_mode(action_id, cfg) -> Mode` – `never`-auto akce vrací max `ask`.
  - `running_vms() -> list[str]` – `virsh -c qemu:///system list --name --state-running`, při chybě `[]`.
- Produces (`actions/update.sh`): `sudo /usr/bin/pacman -Syu --noconfirm`, pak `yay -Sua --noconfirm` (jen pokud `yay` existuje); stdout: řádek „Aktualizováno N balíčků (repo) a M (AUR)“ (počty z `pacman -Qu` před během) nebo při chybě „Aktualizace selhala: <první řádek chyby>“; plný výstup pacmanu na stderr; `.pacnew` v logu pacmanu → druhý řádek „Vznikly soubory .pacnew: N“; kód 1 při selhání pacmanu, i když yay projde.

- [ ] **Step 1: Napsat padající testy `tests/test_actions.py`** – `test_run_action_ok` (skript `echo hotovo`), `test_run_action_failure_keeps_stderr`, `test_run_action_timeout` (monkeypatch `TIMEOUTS` na 0.2 s), `test_needs_confirmation_qemu_with_running_vm` (item `qemu-desktop`, `running_vms=lambda: ["win11-tia"]` → text obsahuje „win11-tia“), `test_needs_confirmation_arch_news_match` (news item packages `["linux"]`, finding items `linux` → důvod), `test_needs_confirmation_none_without_vm`, `test_effective_mode_never_auto` (`cfg.actions = {"remove-package": "auto"}` → `"ask"`).

- [ ] **Step 2: Napsat padající test `tests/test_action_update.py`** přes `shim`: `sudo` jako shim, který spustí zbytek argumentů (`exec "$@"`), `pacman` shim: pro `-Qu` vypíše 3 řádky, pro `-Syu` vypíše „:: Running post-transaction hooks“ a kód 0; `yay` shim vypíše „ there is nothing to do“; assert stdout první řádek `"Aktualizováno 3 balíčků (repo) a 0 (AUR)"`, kód 0. Druhý test: `pacman -Syu` kód 1 se stderr „error: failed to commit transaction (conflicting files)“ → kód 1, stdout začíná „Aktualizace selhala: error: failed to commit“.

- [ ] **Step 3: Spustit, ověřit pád.** Expected: FAIL.

- [ ] **Step 4: Implementovat `actions.py` a `actions/update.sh`.**

- [ ] **Step 5: Spustit testy + shellcheck.** Expected: PASS.

- [ ] **Step 6: Commit** – `git commit -m "feat: běh akcí a akce update"`.

---

### Task 6: Klíč, šablonové a Claude shrnutí

**Files:**
- Create: `arch_assistant/secrets.py`, `arch_assistant/summarizer.py`, `tests/test_secrets.py`, `tests/test_summarizer.py`

**Interfaces:**
- Consumes: `Finding`, `Summary`, `Config`.
- Produces (`secrets.py`): `read_api_key(path: Path) -> str | None` – regex `^\s*(?:export\s+)?ANTHROPIC_API_KEY=["']?([^"'\s]+)`; chybějící soubor, nečitelný soubor nebo bez řádku → `None`.
- Produces (`summarizer.py`):
  - `SEVERITY_LABEL = {"critical": "Kritické", "high": "Důležité", "medium": "Střední", "low": "Drobné"}`
  - `template_summary(findings: list[Finding]) -> Summary` – headline: jeden nález → `f.title`; víc → `"{n} věcí k řešení: {nejvyšší severity label}"`; body: řádky `"• {title} – {summary}"` max 5, zbytek `"… a dalších N"`; risks `[]`.
  - `class SummaryModel(BaseModel)`: `headline: str`, `body: str`, `risks: list[str]` (pydantic).
  - `build_prompt(findings, news_text: str) -> str` – JSON nálezů bez `items` nad 10 položek (nahradit `{"count": n}`), bez `error`; instrukce česky: „Napiš headline (max 60 znaků) a body (max 500 znaků) pro notifikaci správce Arch Linuxu, věcně, bez úvodu; do risks dej jen konkrétní rizika z novinek Arch nebo zásadních balíčků.“
  - `claude_summary(findings, news_text, cfg: Config, api_key: str, log) -> Summary | None` – `anthropic.Anthropic(api_key=...)`, `client.messages.parse(model=cfg.model, max_tokens=1024, output_config={"effort": "low"}, output_format=SummaryModel, messages=[{"role": "user", "content": prompt}])`; `response.parsed_output` → `Summary`; jakákoli výjimka SDK → log warning, `None`; `stop_reason == "refusal"` → `None`. Logovat `usage.input_tokens`/`output_tokens`.
  - `summarize(findings, news_text, cfg, api_key: str | None, log) -> Summary` – bez klíče nebo při `None` šablona.

- [ ] **Step 1: Padající testy `tests/test_secrets.py`** – `export ANTHROPIC_API_KEY="sk-ant-x"` → `"sk-ant-x"`; bez export a bez uvozovek; soubor s jiným obsahem → `None`; neexistující → `None`.

- [ ] **Step 2: Padající testy `tests/test_summarizer.py`** – `test_template_single_finding`, `test_template_many_findings_truncates_to_5`, `test_build_prompt_collapses_long_items` (12 položek → v promptu `"count": 12`, žádné `name`), `test_summarize_without_key_uses_template`, `test_claude_summary_uses_parse` (monkeypatch `anthropic.Anthropic` falešnou třídou, jejíž `messages.parse` vrátí objekt s `parsed_output=SummaryModel(...)`, `stop_reason="end_turn"`, `usage`; assert předané `model == cfg.model`, `max_tokens == 1024`, `output_config == {"effort": "low"}`), `test_claude_summary_exception_returns_none`.

- [ ] **Step 3: Spustit, ověřit pád.** Expected: FAIL.

- [ ] **Step 4: Implementovat `secrets.py`, `summarizer.py`.**

- [ ] **Step 5: Spustit testy.** Expected: PASS.

- [ ] **Step 6: Jedno skutečné volání** (stojí zlomek centu): `python -c` se dvěma umělými nálezy a klíčem ze `secrets.zsh`; Expected: `Summary` s českým headline, log s počty tokenů (vstup pod 1 500).

- [ ] **Step 7: Commit** – `git commit -m "feat: shrnutí nálezů přes Claude se šablonovým záložním režimem"`.

---

### Task 7: Notifikace přes D-Bus

**Files:**
- Create: `arch_assistant/notify.py`, `tests/test_notify.py`

**Interfaces:**
- Consumes: `Summary`, `ActionResult`.
- Produces:
  - `BUTTONS_ASK = [("yes", "Ano"), ("yes_auto", "Ano, příště automaticky"), ("no", "Ne")]`, `BUTTON_MENU = [("menu", "Otevřít nabídku")]`
  - `class Notifier(QObject)` – konstruktor `Notifier(app_name="Arch Assistant", icon="arch-assistant")`; metoda `send(summary: Summary, buttons: list[tuple[str, str]], on_action: Callable[[str], None], on_closed: Callable[[], None] | None = None, urgency: int = 1) -> int` – `QDBusInterface("org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications")`, volání `Notify(app_name, 0, icon, headline, body, actions_flat, {"urgency": urgency, "resident": True}, -1)`; připojí signály `ActionInvoked(uint, str)` a `NotificationClosed(uint, uint)` a volá callbacky jen pro své id; `urgency` 2 pro `critical`.
  - `result_popup(notifier, result: ActionResult, label: str) -> int` – headline `"{label}: hotovo"` / `"{label}: chyba"`, body = `result.output`, urgency 2 při chybě, bez tlačítek, timeout 15 s (`expire_timeout=15000`).
  - `build_body(summary: Summary) -> str` – body + `"\n\nRizika: " + "; ".join(risks)` je-li co.

- [ ] **Step 1: Padající test `tests/test_notify.py`** – `test_build_body_with_risks`, `test_notify_flattens_actions_and_dispatches` (monkeypatch `QDBusInterface` falešnou třídou zachycující argumenty `call("Notify", …)`; assert pole akcí `["yes","Ano","yes_auto","Ano, příště automaticky","no","Ne"]`; pak simulovat `ActionInvoked(id, "yes_auto")` → callback dostal `"yes_auto"`; `ActionInvoked(jiné_id, "yes")` → callback nevolán), `test_closed_without_action_calls_on_closed` (Review Focus 3).

- [ ] **Step 2: Spustit, ověřit pád.** Expected: FAIL.

- [ ] **Step 3: Implementovat `notify.py`.**

- [ ] **Step 4: Spustit testy.** Expected: PASS (testy potřebují `QCoreApplication`; vytvořit ve fixture, `QT_QPA_PLATFORM=offscreen`).

- [ ] **Step 5: Ruční ověření v Plasmě** – `python -c` s `QApplication`, `Notifier().send(Summary("Test", "Tělo", []), BUTTONS_ASK, print)`; kliknout na „Ano“. Expected: popup Plasmy se třemi tlačítky, výstup `yes`.

- [ ] **Step 6: Commit** – `git commit -m "feat: notifikace s tlačítky přes D-Bus"`.

---

### Task 8: Tray aplikace, cyklus a CLI

**Files:**
- Create: `arch_assistant/cycle.py` (třídy `Pending`, `Cycle`, `CycleResult`, bez Qt), `arch_assistant/tray.py` (jen Qt), `arch_assistant/__main__.py`, `bin/arch-assistant`, `assets/tray.svg`, `assets/tray-attention.svg`, `assets/tray-busy.svg`, `tests/test_cycle.py`

**Interfaces:**
- Consumes: vše z Task 1–7.
- Produces (`tray.py`):
  - `@dataclass Pending(finding: Finding, action: Action, reason: str | None)`
  - `class Cycle` (bez Qt, testovatelná): `Cycle(cfg, state, log, api_key, checks_dir, actions_dir, running_vms=running_vms)`; `run(only=None) -> CycleResult(findings, new, summary | None, auto_run: list[ActionResult], pending: list[Pending])`:
    1. `run_all` → `findings`; uložit `state.data["last_findings"]`.
    2. `new = state.new_findings(findings, cfg, now)`; prázdné → vrátit bez shrnutí.
    3. pro každý nález s akcemi: `mode = effective_mode`; `reason = needs_confirmation(...)`; `auto` bez `reason` → `run_action` ihned + `POST_HOOKS` (spustit uvedené kontroly přes `run_all(only=...)` a připojit do `findings`), `mark_update_run` u `update`; `ask` nebo `reason` → `Pending`; `never` → jen `mark_reported`.
    4. `news_text` = summary nálezu `arch-news`; `summary = summarize(new, news_text, …)`; `mark_reported` pro všechna `new`.
  - `class TrayApp(QObject)`: `TrayApp(cfg, state, log, api_key)`; `QSystemTrayIcon` s ikonou `tray.svg`, při `pending` neprázdném `tray-attention.svg`, během cyklu/akce `tray-busy.svg`; `QMenu`: čekající akce (položka `"{label}: {finding.title}"`, klik = spustit; při `reason` tooltip s důvodem), oddělovač, „Zkontrolovat teď“, „Otevřít log“ (`xdg-open`), „Nastavení“ (`xdg-open config.toml`, vytvoří ze šablony, když chybí), „Ukončit“; `QTimer` s `interval_hours`; první cyklus hned po startu (`QTimer.singleShot(0, …)`); cyklus i akce v `QThread`/`QThreadPool`, zámek `cycle_running` – druhý požadavek během běhu se zahodí s logem (Review Focus 2); po cyklu s `summary`: jedna notifikace – u jediného `Pending` tlačítka `BUTTONS_ASK` (`yes` → run, `yes_auto` → `set_action_mode(auto)` + run, `no` → `mark_declined`, zavření bez volby → zůstane v nabídce), u více `BUTTON_MENU`; po každé akci (auto i ruční) `result_popup` + aktualizace nabídky.
- Produces (`__main__.py`): `arch-assistant` (tray), `arch-assistant check [ID…] [--json]` (vypíše nálezy, s `--json` surově), `arch-assistant act ID [ARGS…]` (spustí akci, vypíše výsledek, kód 0/1), `arch-assistant once` (celý `Cycle.run` bez GUI, vypíše shrnutí a seznam `pending`, nic nespouští automaticky kromě režimu `auto`). `bin/arch-assistant`: `#!/bin/sh`, `exec "$HOME/.local/share/arch-assistant/venv/bin/python" -m arch_assistant "$@"` s fallbackem na `python3`, když venv není.
- Ikony: 24 px, `fill="#dedede"` (Simpleton), značka Anthropic jako zjednodušený obrys (ruční kresba, ne stažený soubor); `attention` = tatáž + plná tečka vpravo dole; `busy` = tatáž s poloviční průhledností.

- [ ] **Step 1: Padající test `tests/test_cycle.py`** s dočasnými `checks/` a `actions/` skripty a falešným `summarize` (monkeypatch na šablonu):
  - `test_cycle_quiet_when_nothing_new` (kontrola vrací `none` → `summary is None`, `auto_run == []`),
  - `test_cycle_ask_creates_pending` (nález s akcí, režim výchozí → jeden `Pending`, `summary` není `None`),
  - `test_cycle_auto_runs_action_and_post_hooks` (`cfg.actions["update"]="auto"`, akce `update.sh` → `auto_run[0].ok`, `findings` obsahuje nálezy `secure-boot` a `reboot-needed` z dočasných skriptů, `state.data["last_update_run"]` nastaveno),
  - `test_cycle_auto_blocked_by_reason` (`running_vms=lambda: ["vm"]`, item `qemu-desktop` → `Pending` s `reason`, nic neběželo),
  - `test_cycle_post_hooks_run_even_when_action_fails` (akce `update.sh` končí kódem 1 → `auto_run[0].ok is False`, přesto `findings` obsahuje `secure-boot` a `reboot-needed`; Review Focus 5),
  - `test_cycle_run_is_rejected_while_running` (`Cycle.run` drží `threading.Lock` neblokujícím `acquire`; druhé volání z jiného vlákna během běhu vrátí `None` a zapíše do logu; Review Focus 2),
  - `test_cycle_marks_reported_so_second_run_is_quiet`.

- [ ] **Step 2: Spustit, ověřit pád.** Expected: FAIL.

- [ ] **Step 3: Implementovat `cycle.py`** (`Cycle.run` vrací `CycleResult | None`; `None` = odmítnuto, běží jiný cyklus).

- [ ] **Step 4: Spustit testy.** Expected: PASS.

- [ ] **Step 5: Implementovat `TrayApp`, `__main__.py`, `bin/arch-assistant`, ikony.**

- [ ] **Step 6: Ruční ověření** – `python -m arch_assistant once` (Expected: výpis nálezů a shrnutí, bez GUI); `python -m arch_assistant` (Expected: bílá ikona v tray, nabídka s položkami, po startu proběhne cyklus, při nálezu popup s tlačítky; klik „Ne“ → položka zůstane v nabídce; „Zkontrolovat teď“ dvakrát rychle → druhý běh se zahodí, v logu zpráva).

- [ ] **Step 7: Commit** – `git commit -m "feat: tray aplikace, cyklus kontrol a CLI"`.

---

### Task 9: Kontroly zdraví: `failed-units`, `disk-space`, `coredumps`, `journal-errors` + akce `restart-unit`, `clean-coredumps`

**Files:**
- Create: `checks/failed-units.sh`, `checks/disk-space.sh`, `checks/coredumps.sh`, `checks/journal-errors.sh`, `actions/restart-unit.sh`, `actions/clean-coredumps.sh`, `tests/test_checks_health.py`, `tests/test_actions_health.py`

**Interfaces:** (každá kontrola přes `shim`; `items[*].name` je vždy přítomno)

| Kontrola | Příkazy (shim) | Pravidlo | items | severity | akce |
|---|---|---|---|---|---|
| `failed-units` | `systemctl --failed --plain --no-legend`, `systemctl --user --failed …`, `systemctl [--user] show -p NRestarts --value <unit>` pro aktivní jednotky typu service s `NRestarts >= 5` (seznam z `systemctl [--user] list-units --type=service --state=running --plain --no-legend`) | každá padlá nebo restartující jednotka | `{name, scope: "system"\|"user", state: "failed"\|"restarting", restarts}` | `high` | `restart-unit <scope> <name>` – jen pro `user` jednotky automaticky možné; `system` jednotky mají akci také, ale `effective_mode` v Task 5 rozšířit: `restart-unit` s `args[0] == "system"` nikdy `auto` |
| `disk-space` | `df --output=target,pcent,avail -B1M` | `/`, `/home`, `/mnt/data` volné < `AA_DISK_PCT`; `/boot/efi` volné < `AA_ESP_MB` | `{name: mount, free_pct, free_mb}` | `high` | žádná |
| `coredumps` | `coredumpctl list --json=short --since <last_run>` (`last_run` z `$AA_STATE_DIR/state.json` → `last_cycle_at`, jinak `-7days`), `du -sm /var/lib/systemd/coredump` | nové coredumpy > 0 | `{name: exe, count, size_mb(celkem v prvním)}` | `medium` | `clean-coredumps` → `sudo /usr/bin/coredumpctl` nemá delete; akce smaže `sudo /usr/bin/find /var/lib/systemd/coredump -type f -delete` a vypíše uvolněné MB |
| `journal-errors` | `journalctl -b -p err --output=json --no-pager` | počet > `AA_JOURNAL_ERR` | top 10 `{name: MESSAGE[:120], count}` | `low` | žádná |

- [ ] **Step 1: Padající testy `tests/test_checks_health.py`** – po jednom pozitivním a jednom „nic“ testu na kontrolu, s přesnými vstupy ze sloupce Příkazy (např. `failed-units`: `baloo_file_extractor.service` s `NRestarts=12` → item `state == "restarting"`, `restarts == 12`; `disk-space`: `/boot/efi 90% 25` → item `name == "/boot/efi"`, `/ 50% 200000` nehlásit).

- [ ] **Step 2: Padající testy `tests/test_actions_health.py`** – `restart-unit user kde-baloo.service` volá `systemctl --user restart kde-baloo.service` (shim zapíše argumenty do souboru) a vypíše „Jednotka kde-baloo.service restartována“; `restart-unit system x.service` volá `sudo /usr/bin/systemctl restart x.service`; `clean-coredumps` vypíše „Smazáno N souborů, uvolněno M MB“. Plus test v `tests/test_actions.py`: `effective_mode("restart-unit", cfg, args=("system", "x"))` → `"ask"` i při `auto` (rozšířit signaturu o `args: tuple[str, ...] = ()`).

- [ ] **Step 3: Spustit, ověřit pád.** Expected: FAIL.

- [ ] **Step 4: Implementovat 4 kontroly a 2 akce, upravit `effective_mode`.**

- [ ] **Step 5: Testy + shellcheck.** Run: `python -m pytest -q && shellcheck checks/*.sh actions/*.sh`. Expected: PASS.

- [ ] **Step 6: Ruční ověření:** `arch-assistant check failed-units disk-space coredumps journal-errors`. Expected: validní JSON, hodnoty odpovídají `systemctl --failed` a `df`.

- [ ] **Step 7: Commit** – `git commit -m "feat: kontroly zdraví systému"`.

---

### Task 10: Kontroly bootu a balíčků: `secure-boot`, `reboot-needed`, `pacnew`, `mirrors`, `vuln-packages`, `timeshift`, `clocksource` + akce `refresh-mirrors`, `pacdiff`

**Files:**
- Create: `checks/secure-boot.sh`, `checks/reboot-needed.sh`, `checks/pacnew.sh`, `checks/mirrors.sh`, `checks/vuln-packages.sh`, `checks/timeshift.sh`, `checks/clocksource.sh`, `actions/refresh-mirrors.sh`, `actions/pacdiff.sh`, `tests/test_checks_boot.py`, `tests/test_actions_boot.py`

**Interfaces:**

| Kontrola | Příkazy | Pravidlo | severity | akce |
|---|---|---|---|---|
| `secure-boot` | `sudo /usr/bin/sbctl verify`, `bootctl -p` (ESP), `ls <esp>/EFI/Linux/*.efi`; hlásit vždy, když `$AA_STATE_DIR/state.json` má `last_update_run` novější než `last_secure_boot_report` **nebo** když `sbctl verify` hlásí nepodepsaný soubor | nepodepsaný nebo chybějící UKI → `critical`, titulek „UKI není podepsaný: <soubor>“; po aktualizaci vše v pořádku → `low`, titulek „Secure Boot v pořádku po aktualizaci“ | viz pravidlo | žádná (summary odkazuje na `custom-arch-setup-script/pc/secureboot-tpm2.sh`) |
| `reboot-needed` | `uname -r`, `pacman -Q linux linux-lts`, `pacman -Qi systemd mesa qt6-base` (`Install Date`), `uptime -s` | běžící jádro (podle názvu balíčku odvozeného z `uname -r`: obsahuje `-lts` → `linux-lts`) ≠ nainstalovaná verze; nebo install date některého z balíčků > boot time | `medium` | žádná |
| `pacnew` | `sudo /usr/bin/pacdiff -o` | aspoň jeden soubor | `medium` | `pacdiff` – otevře terminál (`ghostty -e sudo /usr/bin/pacdiff`), nikdy auto (`NEVER_AUTO`) |
| `mirrors` | `stat -c %Y /etc/pacman.d/mirrorlist`, `systemctl is-active reflector.timer` | stáří > `AA_MIRROR_DAYS` dní | `low` | `refresh-mirrors` → `sudo /usr/bin/reflector --country Czechia,Germany --protocol https --latest 20 --sort rate --save /etc/pacman.d/mirrorlist` |
| `vuln-packages` | `arch-audit --format "%n %c %s"` (nainstalovat `arch-audit` v Task 13) | aspoň jeden; `high` když řádek obsahuje fixed verzi (`arch-audit -u` = upgradable) | `medium`/`high` | žádná (odkaz na `update`) |
| `timeshift` | `sudo /usr/bin/timeshift --list` | žádný snapshot nebo poslední starší než 2 dny | `medium` | žádná |
| `clocksource` | `cat /sys/devices/system/clocksource/clocksource0/current_clocksource` | ≠ `tsc` | `medium`, summary: „Jádro přepnulo na <x>; pomůže úplné vypnutí a zapnutí, trvale `tsc=reliable`“ | žádná |

- [ ] **Step 1: Padající testy `tests/test_checks_boot.py`** – pro každou kontrolu pozitivní + „nic“ (sysfs u `clocksource` přes env `AA_CLOCKSOURCE_FILE` ukazující do tmp; `secure-boot` se stavem `last_update_run` novějším → test `low` „v pořádku“; `sbctl` výstup s `✗ … is not signed` → `critical`).

- [ ] **Step 2: Padající testy `tests/test_actions_boot.py`** – `refresh-mirrors` volá reflector s přesnými argumenty a vypíše „Mirrorlist obnoven (N serverů)“; `pacdiff` spustí `ghostty -e …` (shim) a vypíše „Otevřen pacdiff v terminálu“.

- [ ] **Step 3: Spustit, ověřit pád.** Expected: FAIL.

- [ ] **Step 4: Implementovat.** `secure-boot` zapisuje `last_secure_boot_report` do `$AA_STATE_DIR/secure-boot.json` (ne do `state.json`, aby kontroly nesahaly do stavu jádra; `Cycle` to nepotřebuje).

- [ ] **Step 5: Testy + shellcheck.** Expected: PASS.

- [ ] **Step 6: Ruční ověření:** `arch-assistant check secure-boot reboot-needed pacnew mirrors vuln-packages timeshift clocksource`. Expected: validní JSON; `clocksource` = `none` při `tsc`.

- [ ] **Step 7: Commit** – `git commit -m "feat: kontroly bootu, balíčků a mirrorů"`.

---

### Task 11: Kontroly balastu: `pkg-cache`, `orphans`, `jetbrains-old`, `user-cache` + akce `clean-pkg-cache`, `remove-orphans`, `clean-jetbrains-old`, `clean-user-cache`

**Files:**
- Create: `checks/pkg-cache.sh`, `checks/orphans.sh`, `checks/jetbrains-old.py`, `checks/user-cache.sh`, `actions/clean-pkg-cache.sh`, `actions/remove-orphans.sh`, `actions/clean-jetbrains-old.py`, `actions/clean-user-cache.sh`, `tests/test_checks_clutter.py`, `tests/test_actions_clutter.py`

**Interfaces:**

| Kontrola | Zdroj | Pravidlo | items | akce |
|---|---|---|---|---|
| `pkg-cache` | `du -sm /var/cache/pacman/pkg ~/.cache/yay` | součet > `AA_PKG_CACHE_GB` GB | `{name: path, size_mb}` | `clean-pkg-cache` → `sudo /usr/bin/paccache -rk2` + `yay -Sc --noconfirm`; výstup „Uvolněno N MB“ (rozdíl `du` před/po) |
| `orphans` | `pacman -Qdtq` | aspoň jeden | `{name}` | `remove-orphans` → `sudo /usr/bin/pacman -Rns --noconfirm <args>`; args = jména z nálezu |
| `jetbrains-old` | adresáře `~/.config/JetBrains`, `~/.local/share/JetBrains`, `~/.cache/JetBrains` (kořen přes env `AA_HOME`, výchozí `$HOME`), vzor `^([A-Za-z]+)(\d{4}\.\d)$`; Toolbox: `~/.local/share/JetBrains/Toolbox/apps/<ide>/<verze>` | pro každou řadu (`IntelliJIdea`, `GoLand`…) všechny verze kromě nejvyšší | `{name: "GoLand2025.2", size_mb, paths: [...]}` | `clean-jetbrains-old <name…>` – smaže jen `paths` uvedené v nálezu (akce si je znovu spočítá stejnou funkcí a odmítne jméno, které je nejvyšší verze); výstup „Smazáno: GoLand2025.2 (1 800 MB), …“ |
| `user-cache` | `du -d1 -m ~/.cache` | položka > `AA_USER_CACHE_GB` GB | `{name: dirname, size_mb, safe: bool}`; `safe` pro `yay, pip, npm, go-build, google-chrome, chromium, mozilla, mesa_shader_cache, JetBrains` | `clean-user-cache <name…>` – jen `safe` jména (seznam `SAFE_CACHES` sdílený: akce si ověří a jiné jméno odmítne s kódem 1); `rm -rf ~/.cache/<name>` |

- [ ] **Step 1: Padající testy `tests/test_checks_clutter.py`** – `jetbrains-old`: v `tmp_path` vytvořit `.config/JetBrains/GoLand2025.2`, `GoLand2026.1`, `IntelliJIdea2026.2` se soubory → item jen `GoLand2025.2`; `user-cache`: shim `du` → položka `yay 1500` s `safe == True`, `foo 2000` se `safe == False`; `pkg-cache`, `orphans` pozitivní + „nic“.

- [ ] **Step 2: Padající testy `tests/test_actions_clutter.py`** – `clean-jetbrains-old GoLand2025.2` v `AA_HOME` smaže adresář a ponechá `GoLand2026.1`; `clean-jetbrains-old GoLand2026.1` (nejvyšší) → kód 1, stdout „Odmítnuto: GoLand2026.1 je aktuální verze“; `clean-user-cache foo` → kód 1 „Odmítnuto: foo není v seznamu bezpečných cache“; `remove-orphans a b` volá pacman s `-Rns --noconfirm a b`.

- [ ] **Step 3: Spustit, ověřit pád.** Expected: FAIL.

- [ ] **Step 4: Implementovat.** Sdílenou logiku `jetbrains-old` (výčet verzí) dát do `checks/_jetbrains.py` a importovat z kontroly i akce (`sys.path` na adresář repa).

- [ ] **Step 5: Testy + shellcheck.** Expected: PASS.

- [ ] **Step 6: Ruční ověření:** `arch-assistant check pkg-cache orphans jetbrains-old user-cache`. Expected: `jetbrains-old` najde skutečné staré verze (na stroji jsou `GoLand2025.2`, `GoLand2026.1`).

- [ ] **Step 7: Commit** – `git commit -m "feat: kontroly balastu a úklidové akce"`.

---

### Task 12: Kontroly `resource-hogs`, `unused-apps`, `docker-vm` + akce `remove-package`

**Files:**
- Create: `checks/resource-hogs.py`, `checks/unused-apps.py`, `checks/docker-vm.sh`, `actions/remove-package.sh`, `tests/test_checks_misc.py`

**Interfaces:**

| Kontrola | Zdroj | Pravidlo | severity | akce |
|---|---|---|---|---|
| `resource-hogs` | `ps -eo pid,comm,pcpu,rss,etimes --no-headers`; baseline `$AA_STATE_DIR/hogs.json`: klouzavý průměr `pcpu` a `rss` po `comm` (α = 0.2), aktualizuje se při každém běhu | proces s `pcpu > AA_HOG_CPU_PCT` a `etimes > AA_HOG_MINUTES*60`, nebo `rss > AA_HOG_RSS_GB`; ignorovat `comm` ze seznamu `IGNORE = {"qemu-system-x86", "java", "idea", "goland", "chrome", "firefox", "node", "electron"}` pokud je hodnota do 2× baseline | `medium`, items `{name: comm, pid, cpu_pct, rss_mb, baseline_cpu}` | žádná (nikdy nezabíjet) |
| `unused-apps` | `pacman -Qqe` + `pacman -Ql <pkg>` filtr `/usr/share/applications/*.desktop`; poslední použití = max(`atime` binárek z `Exec=`, záznamy v `~/.local/share/recently-used.xbel`); ignorovat balíčky ze seznamu `KEEP = {"plasma-*", "kde-*", "systemd", "linux*"}` | poslední použití > `AA_UNUSED_DAYS` dní | `low`, items `{name: pkg, app: Name z .desktop, last_used: date}` | `remove-package <pkg>` → `sudo /usr/bin/pacman -Rns --noconfirm <pkg>` (v `NEVER_AUTO`) |
| `docker-vm` | `docker system df --format json` (když `docker` běží), `virsh -c qemu:///system list --all --name`, `virsh domblklist`, `qemu-img info --backing-chain` (přes `sudo`), soubory `/mnt/data/vm/*.qcow2` | nepoužité image/volumes > 1 GB; qcow2 soubory mimo řetěz jakéhokoli domainu | `low`, items `{name, kind: "image"\|"volume"\|"vm-overlay", size_mb}` | **žádná** |

- [ ] **Step 1: Padající testy `tests/test_checks_misc.py`** – `resource-hogs`: shim `ps` s procesem `baloo_file_ext 95.0 200000 900` → item; druhý běh s `baseline_cpu` v `hogs.json`; `java` s 60 % a baseline 40 → nehlásit; `unused-apps`: tmp `.desktop` + binárka s `atime` před 200 dny (`os.utime`) → item; `docker-vm`: shim `virsh` (jeden domain, řetěz 2 soubory) a tmp adresář `AA_VM_DIR` se 3 qcow2 → jeden `vm-overlay` item.

- [ ] **Step 2: Spustit, ověřit pád.** Expected: FAIL.

- [ ] **Step 3: Implementovat.**

- [ ] **Step 4: Testy + shellcheck.** Expected: PASS.

- [ ] **Step 5: Ruční ověření:** `arch-assistant check resource-hogs unused-apps docker-vm`. Expected: `docker-vm` nehlásí `win11-tia.*.qcow2` v aktivním řetězu (`win11-tia.qcow2` → `pred-tia` → `tia-v21` → `task10`).

- [ ] **Step 6: Commit** – `git commit -m "feat: žrouti, nepoužívané aplikace, balast Dockeru a VM"`.

---

### Task 13: Instalace, autostart, náhrada Octopi, README

**Files:**
- Create: `install.sh`, `assets/arch-assistant.desktop` (autostart), `assets/config.example.toml`, `README.md`
- Modify: `arch_assistant/tray.py` (položka „Nastavení“ kopíruje `config.example.toml`, pokud `config.toml` chybí)

**Interfaces:**
- `install.sh` (idempotentní, bez interakce kromě `sudo`): 1) `sudo pacman -S --needed --noconfirm pacman-contrib arch-audit python-pipx` (ověřit názvy), 2) venv `~/.local/share/arch-assistant/venv` s `--system-site-packages`, `pip install -e <repo>` (bez `[dev]`), 3) symlink `~/.local/bin/arch-assistant` → `bin/arch-assistant`, 4) `~/.config/autostart/arch-assistant.desktop` (`Exec=<abs>/bin/arch-assistant`, `Icon=<abs>/assets/tray.svg`, `X-KDE-autostart-after=panel`), 5) `~/.config/arch-assistant/config.toml` z příkladu, pokud chybí, 6) odinstalovat `octopi` (`sudo pacman -Rns --noconfirm octopi`, jen je-li nainstalované; nejdřív `pkill octopi-notifier`), 7) spustit `arch-assistant` na pozadí, pokud neběží. Přepínač `--no-octopi` krok 6 vynechá.
- `README.md` (česky): co to dělá, instalace, konfigurace (tabulka prahů a režimů), kontrakt skriptů (zkráceně, odkaz na spec), ladění (`check`, `act`, `once`, log), poznámka o `sudo` a klíči.

- [ ] **Step 1: Test `tests/test_install.py`** – `bash -n install.sh` projde; `shellcheck install.sh` bez nálezů; `install.sh --dry-run` (přidat přepínač: jen vypíše kroky) vypíše 7 řádků začínajících číslem.

- [ ] **Step 2: Implementovat `install.sh`, `.desktop`, `config.example.toml` (všechny klíče z DEFAULTS s komentáři česky), README, úpravu „Nastavení“.**

- [ ] **Step 3: Spustit instalaci na stroji:** `./install.sh`. Expected: venv vytvořen, `~/.local/bin/arch-assistant` funguje, autostart soubor existuje, `octopi` odinstalované (`pacman -Q octopi` → chyba), ikona v tray.

- [ ] **Step 4: Ověřit autostart:** odhlásit a přihlásit (dělá uživatel). Expected: ikona v tray do několika sekund, v logu záznam cyklu.

- [ ] **Step 5: Spustit celou sadu.** Run: `python -m pytest -q && shellcheck checks/*.sh actions/*.sh install.sh`. Expected: PASS.

- [ ] **Step 6: Commit** – `git commit -m "feat: instalace, autostart, náhrada Octopi, README"`. Pak zveřejnit: `gh repo create JakubKrhovjak/arch-assistant --private --source . --push` (soukromé repo; na veřejné přepne uživatel).

---

## Pozdější napojení (mimo tento plán)

`custom-arch-setup-script/post-install.sh`: naklonovat `arch-assistant` a zavolat `install.sh`. Až po ověření na tomto stroji.
