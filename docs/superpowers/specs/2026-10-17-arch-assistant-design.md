# Arch Assistant – návrh

Datum: 2026-10-17. Stav: schváleno uživatelem 2026-10-17 („tak pokračujme“).

## 1. Záměr a proč

Malý „správce stroje“ pro desktop uživatele (Arch Linux, KDE Plasma 6 na Waylandu, Ryzen 9 7900X, 30 GB RAM, UKI + Secure Boot + TPM2, VM `win11-tia` v libvirtu, Docker a k3s). Po přihlášení se tiše spustí do systémové lišty (tray), projde stroj hotovými skripty a ozve se **jen když je co řešit**: čekající aktualizace, nezdravý systém, něco zbytečně žere prostředky, nebo se nahromadil balast. Uživatel rozhoduje jedním kliknutím v notifikaci, nástroj to provede a vždy ohlásí výsledek.

Proč: uživatel nechce sledovat, které balíčky se aktualizují, ani ručně hledat staré verze JetBrains IDE, cache a padající služby. Chce rozhodnutí „ano / ne / příště automaticky“ a jistotu, že se dozví, co se stalo. Dnes tuhle roli částečně plní `octopi-notifier` (jen aktualizace); nástroj ho nahradí a Octopi se odinstaluje.

Claude (API) se používá **jen** na převod surových nálezů do srozumitelného shrnutí a na posouzení novinek z archlinux.org. Detekce i akce jsou deterministické skripty. Spotřeba tokenů má být minimální: v běžný den nula, jinak jedno volání na jeden běh.

## 2. Cíle, ne-cíle, anti-cíle

**Cíle**
- Po přihlášení a pak periodicky zkontrolovat stroj a dát vědět jen při nálezu.
- Každý nález má přiřazenou akci, kterou lze spustit kliknutím, nebo nechat na později v nabídce ikony.
- Pro každý typ akce jde nastavit výchozí chování: ptát se / automaticky ano / nikdy.
- Po každé provedené akci přijde popup s výsledkem (úspěch i chyba), vždy, i u automatických.
- Nahradit `octopi-notifier`.
- Minimální tokeny: Claude dostane jen zhuštěný JSON, výchozí model Haiku 5.5, Sonnet 5.5 volitelně.

**Ne-cíle**
- Není to chat ani obecný asistent. Žádné volné dotazy, žádné agentní procházení systému Claudem.
- Není to správce balíčků s GUI (prohlížení, hledání, instalace balíčků). Na to je terminál.
- Neřeší zálohy ani konfiguraci systému (to dělá `custom-arch-setup-script`).

**Anti-cíle (selhání, i kdyby to „fungovalo“)**
- Smazat nebo aktualizovat cokoli bez kliknutí nebo bez explicitního „automaticky“ v konfiguraci.
- Smazat něco nevratného s vysokou cenou chyby: overlay snapshoty VM, Docker volumes, Timeshift snapshoty, data v `/mnt/data`. Tyhle věci se jen hlásí.
- Automatická aktualizace, která rozbije boot (nepodepsaný UKI po aktualizaci jádra) a nikdo se o tom nedozví.
- Notifikace, které otravují: dvakrát to samé, nebo hlášení při každém přihlášení bez změny stavu.
- Tiché volání Claude i když není co hlásit, nebo posílání dlouhých výpisů.

## 3. Omezení

- Prostředí: Arch, Plasma 6 Wayland, Python 3 a PySide6 6.12 už jsou nainstalované. `yay` jako AUR helper, `checkupdates` z `pacman-contrib`, Timeshift, `sbctl`.
- Root pouze přes pravidlo v sudoers pro přesně vyjmenované příkazy (rozhodnutí uživatele, § 4). Žádný pkexec, žádné ptaní na heslo za běhu.
- Spuštění ve stejném sezení jako Plasma (potřebuje D-Bus sezení pro tray a notifikace).
- Claude API: klíč nebo profil uživatele; nástroj musí fungovat i bez něj (šablonový text místo shrnutí).
- Jazyk uživatelského rozhraní a notifikací: čeština.

## 4. Rozhodnutí uživatele

Tyto body uživatel řekl výslovně, stavitel je nemění:

1. **Ikona v tray**: logo Anthropic, **bílé**, jednobarevné ve stylu motivu Simpleton (ostatní ikony panelu jsou bílé obrysy, barva `#dedede`).
2. **Detekci dělají hotové skripty** (bash nebo Python, na jazyku nezáleží). Claude dostane jen jejich výstup. Tokeny držet na minimu.
3. **Model**: Haiku nebo Sonnet, nic dražšího. Výchozí `claude-haiku-5-5`, volitelně `claude-sonnet-5-5`.
4. **Chování při nálezu**: notifikace (popup Plasmy) s tlačítky **Ano / Ne** a volbou **„příště automaticky“**. Úkoly jde nechat v nabídce ikony na později. **Vždy** přijde popup, že akce proběhla a jak dopadla.
5. **Root**: stroj už má `/etc/sudoers.d/redo` s `NOPASSWD: ALL` a uživatel ho **nechává** (varianta A, 2026-10-17, po vysvětlení rizika; je i ve skupině `docker`, což je root tak jako tak). Asistent tedy volá `sudo` přímo, žádný vlastní soubor v `sudoers.d` nevzniká. § 8 popisuje, které příkazy pod rootem běží, aby bylo možné později přejít na úzký seznam.
9. **Klíč k API** je v `~/.config/zsh/secrets.zsh` (práva 600) jako `ANTHROPIC_API_KEY`, klíč z Console s fakturací. Asistent ho při startu čte z tohoto souboru (cesta v konfiguraci), nikam ho nekopíruje.
10. **Start hned po přihlášení** a pozdější napojení na instalační skript stroje (§ 12).
6. **Notifikace aktualizací**: nezajímá ho seznam balíčků. Vypíchnout jen zásadní (jádro, Plasma apod.), zbytek jako počet.
7. **Nahrazuje `octopi-notifier`.**
8. **Rozsah kontrol** (§ 6) uživatel schválil celý, včetně doplňků navržených Claudem.

## 5. Moje volby (stavitel je může změnit jen s dobrým důvodem)

- **Jazyk a GUI**: Python 3, PySide6. `QSystemTrayIcon` (StatusNotifier na Plasmě), nabídka `QMenu`. Notifikace přes D-Bus `org.freedesktop.Notifications` (QtDBus), protože jen tak jdou do popupu tlačítka (akce) a volba „příště automaticky“ (jako třetí tlačítko „Ano, příště automaticky“).
- **Kontroly a akce jako samostatné skripty** v `checks/` a `actions/` s pevným kontraktem (§ 7). Jdou pouštět ručně z terminálu, bez GUI, což je i způsob testování.
- **Volání Claude**: oficiální Python SDK `anthropic`, model z konfigurace, `output_config.effort = "low"`, `max_tokens` 1024, structured output (JSON se shrnutím a riziky). Jedno volání na běh, jen když existuje aspoň jeden nález. Vstup: zhuštěný JSON nálezů (bez seznamů souborů, jen počty a velikosti) plus text novinek Arch z posledních 30 dnů. Odhad: pod 3 000 vstupních tokenů, tedy řádově desetiny centu za běh na Haiku 5.5 (0,10 USD / 1M vstup, 0,50 USD / 1M výstup).
- **Bez Claude**: když chybí klíč nebo volání selže, notifikace se poskládá ze šablon. Nástroj je plně použitelný i tak.
- **Spuštění**: XDG autostart (`~/.config/autostart/arch-assistant.desktop`). Asistent naběhne **hned po přihlášení** (rozhodnutí uživatele): ikona v tray se objeví okamžitě, kontroly se rozběhnou ihned na pozadí s nízkou prioritou (`nice`, `ionice`), aby nezpomalily start Plasmy. Pak každých 6 hodin (konfigurovatelné). Ruční „Zkontrolovat teď“ v nabídce.
- **Konfigurace**: `~/.config/arch-assistant/config.toml` (model, interval, defaulty akcí, prahy, seznam zásadních balíčků). **Stav** (poslední výsledky, co už bylo hlášeno, baseline žroutů): `~/.local/state/arch-assistant/`. **Log**: `~/.local/state/arch-assistant/log/`.
- **Deduplikace**: nález se hlásí znovu jen při změně (nová verze, větší velikost, nový soubor) nebo po 7 dnech. Co uživatel odmítl („Ne“), se u stejného stavu znovu neptá, zůstane jen v nabídce.
- **Zvláštní pravidlo pro `updates`** (moje volba, protože na Archu přibývají balíčky skoro denně a doslovná deduplikace by hlásila pořád): nová notifikace jen když se objeví **zásadní balíček**, nebo čeká **20 a víc** balíčků, nebo uplynulo **7 dní** od posledního hlášení. „Ne“ platí do té doby. Do té doby se Claude nevolá, takže platí „v běžný den nula tokenů“. Prahy jsou v konfiguraci.
- **Klíč k API**: čte se při startu z `~/.config/zsh/secrets.zsh` (řádek `ANTHROPIC_API_KEY=…`), cesta v `config.toml` jako `api_key_file`. Drží se jen v paměti procesu.
- **Závislosti** (ověřeno 2026-10-17): `pyside6` je nainstalované; SDK `anthropic` není v repu, instaluje se z AUR (`python-anthropic`) nebo do venv v `~/.local/share/arch-assistant/venv` (stavitel zvolí, venv je nezávislý na AUR); `arch-audit` je v repu `extra`. ESP je připojená na **`/boot/efi`** a má jen 299 MB, práh pro ni je 30 MB volného místa, ne 1 GB. Jsou nainstalována dvě jádra (`linux`, `linux-lts`), `reboot-needed` porovnává běžící jádro s tím, ze kterého se bootovalo.
- **Instalace**: skript `install.sh` v repu (symlinky, autostart, sudoers přes `sudo install` do `/etc/sudoers.d/arch-assistant`, odinstalace `octopi`). Repo: `~/projects/arch-assistant`, zveřejnit na GitHubu pod stejným jménem. Balíček pro pacman není cíl.
- **Distribuce ikony**: SVG v repu (`assets/tray.svg`), bílá značka Anthropic nakreslená ručně v témže stylu jako ikony motivu Simpleton (24 px, `currentColor`). Druhá varianta s tečkou pro stav „je co řešit“ a třetí pro „něco běží“.

## 6. Kontroly

Každá kontrola je skript. Sloupec „Akce“ uvádí, co nabídne; „jen hlásit“ znamená bez akce.

| ID | Co | Zdroj | Závažnost | Akce |
|---|---|---|---|---|
| `updates` | Čekající aktualizace repo + AUR; vypíchnout zásadní balíčky (výchozí seznam: `linux*`, `linux-firmware`, `systemd`, `plasma-*`, `kwin`, `mesa`, `qemu*`, `libvirt`, `docker`, `k3s`, `nvidia*`/`amdgpu` dle HW) | `checkupdates`, `yay -Qua` | info; vysoká když jádro / systemd | `update` (celý systém) |
| `arch-news` | Novinky z archlinux.org s „manual intervention“ od posledního updatu | RSS feed | vysoká | jen hlásit; při shodě s čekajícím balíčkem **vynutí dotaz** i když je `update` nastaven na „automaticky“ |
| `pacnew` | Soubory `.pacnew` / `.pacsave` | `pacdiff -o` | střední | otevřít diff v terminálu (`pacdiff` jako uživatel přes `sudo`), ne automaticky |
| `reboot-needed` | Běžící jádro ≠ nainstalované; aktualizovaný systemd / Mesa / Qt od posledního startu | `uname -r` vs `pacman -Q linux`, `pacman -Qi` install dates vs `uptime -s` | střední | jen hlásit |
| `secure-boot` | Po aktualizaci jádra: UKI v ESP existuje a je podepsaný | `sbctl verify`, soubory v `/boot/efi` (`bootctl -p`) | kritická při chybě; **hlásit i úspěch** po updatu jádra | jen hlásit (oprava je ruční, odkaz na `custom-arch-setup-script`) |
| `failed-units` | Padlé nebo restartující jednotky systemd (system i user), crash loop jako Baloo | `systemctl --failed`, `systemctl --user --failed`, počet restartů z `systemctl show -p NRestarts` | vysoká | `restart-unit` (jen user jednotky automaticky, system jednotky jen po kliknutí) |
| `disk-space` | Volné místo na `/`, `/boot/efi`, `/mnt/data`, `/home` pod prahem (výchozí 10 %, 30 MB pro ESP) | `df` | vysoká | jen hlásit, odkáže na kontroly balastu |
| `coredumps` | Nové coredumpy od posledního běhu, celková velikost | `coredumpctl list --json` | střední | `clean-coredumps` |
| `journal-errors` | Chyby v journalu od posledního startu nad prahem (výchozí 50) a nové oproti baseline | `journalctl -b -p err --output=json` | nízká | jen hlásit (Claude dostane 10 nejčastějších zpráv zkrácených na 120 znaků) |
| `timeshift` | Stáří posledního snapshotu, selhání plánu | `timeshift --list` | střední | jen hlásit |
| `clocksource` | `current_clocksource` není `tsc` | `/sys/devices/system/clocksource/clocksource0/` | střední | jen hlásit (zná řešení: vypnout a zapnout, `tsc=reliable`) |
| `vuln-packages` | Nainstalované balíčky se známou zranitelností | `arch-audit` (nainstalovat jako závislost) | střední | jen hlásit; vysoká, když existuje oprava v repu (= `update`) |
| `mirrors` | Mirrorlist starší než 7 dní | mtime `/etc/pacman.d/mirrorlist`, stav `reflector.timer` | nízká | `refresh-mirrors` |
| `resource-hogs` | Proces s CPU > 50 % po dobu > 5 min nebo RSS > 4 GB mimo známé (IDE, VM, prohlížeč); porovnání s baseline průměrů po procesech | `ps`, `/proc`, baseline ve stavu | střední | jen hlásit s názvem procesu a PID; nikdy nezabíjet |
| `pkg-cache` | Velikost cache pacmanu a yay nad prahem (výchozí 2 GB) | `du` na `/var/cache/pacman/pkg`, `~/.cache/yay` | nízká | `clean-pkg-cache` (`paccache -rk2`, `yay -Sc`) |
| `orphans` | Osiřelé balíčky | `pacman -Qdtq` | nízká | `remove-orphans` |
| `jetbrains-old` | Staré verze IDE v `~/.config/JetBrains`, `~/.local/share/JetBrains`, `~/.cache/JetBrains` (každá řada má novější verzi), plus staré buildy v Toolboxu | adresáře podle vzoru `<IDE><rok.verze>`, `~/.local/share/JetBrains/Toolbox/apps` | nízká | `clean-jetbrains-old` (jen verze, ke kterým existuje novější, nikdy aktuální) |
| `user-cache` | Položky v `~/.cache` nad prahem (výchozí 1 GB) | `du -d1` | nízká | `clean-user-cache <název>` (jen vyjmenované bezpečné: prohlížeče, yay, pip, npm, go build; ostatní jen hlásit) |
| `unused-apps` | Balíčky s `.desktop` položkou, které nebyly spuštěny 90+ dní | historie z `~/.local/share/user-places.xbel` a `recently-used.xbel`, atime binárek | nízká | jen hlásit s názvem balíčku; odinstalace po kliknutí (`remove-package`) |
| `docker-vm` | Nepoužívané Docker image a volumes, osiřelé overlay soubory VM v `/mnt/data/vm` (nejsou v řetězu žádného domainu) | `docker system df`, `virsh domblklist`, `qemu-img info --backing-chain` | nízká | **jen hlásit**, nikdy nemazat (anti-cíl) |

Kontroly běží paralelně s timeoutem 60 s na každou. Kontrola, která spadne, se hlásí jako „kontrola selhala“ s nízkou závažností, neblokuje ostatní.

## 7. Kontrakt skriptů

**Kontrola** (`checks/<id>.sh` nebo `.py`): bez argumentů, čte konfiguraci z proměnných prostředí `AA_*` (prahy), tiskne na stdout jeden JSON:

```json
{
  "id": "jetbrains-old",
  "severity": "low",
  "title": "Staré verze JetBrains IDE: 3,2 GB",
  "summary": "GoLand 2025.2, IntelliJ 2026.1 (aktuální 2026.2)",
  "items": [{"name": "GoLand2025.2", "size_mb": 1800}],
  "actions": [{"id": "clean-jetbrains-old", "label": "Smazat staré verze", "args": ["GoLand2025.2", "IntelliJIdea2026.1"]}]
}
```

Prázdný nález = `severity: "none"`. Návratový kód 0 i při nálezu; nenulový jen při chybě skriptu.

**Akce** (`actions/<id>.sh`): argumenty z `args`, běží jako uživatel, root si bere přes `sudo` jen na vyjmenované příkazy. Tiskne krátký lidský výsledek na stdout (1 až 3 řádky, jde do popupu), detail na stderr (jde do logu). Návratový kód 0 = úspěch. Timeout 30 minut (aktualizace), jinak 5 minut.

**Výchozí režimy akcí** (`config.toml`, `[actions]`): `update = "ask"`, `restart-unit = "ask"`, `clean-coredumps = "ask"`, `clean-pkg-cache = "ask"`, `remove-orphans = "ask"`, `clean-jetbrains-old = "ask"`, `clean-user-cache = "ask"`, `refresh-mirrors = "ask"`, `remove-package = "ask"`. Hodnoty: `ask` / `auto` / `never`. Tlačítko „Ano, příště automaticky“ zapíše `auto`.

## 8. Co běží pod rootem

Root se bere přes existující `sudo` bez hesla (§ 4 bod 5). Pro přehled a pro případný pozdější úzký seznam jsou to tyto příkazy:

- akce: `pacman -Syu --noconfirm`, `pacman -Rns --noconfirm <balíčky>`, `paccache -rk2`, `pacdiff`, `reflector …`, `systemctl restart <jednotka>` (system), `coredumpctl …`
- kontroly: `sbctl verify`, `timeshift --list`, `qemu-img info --backing-chain` (overlay soubory VM vlastní `libvirt-qemu`), `pacdiff -o` (root-only adresáře v `/etc`)

AUR aktualizace běží jako uživatel (`yay -Sua --noconfirm`), yay si interně volá `sudo pacman -U`. Aktualizace jádra spouští pacman hooky pro UKI a podpis (už existují ze setup skriptu); kontrola `secure-boot` běží po každé akci `update`.

## 9. Průběh

1. Autostart → ikona v tray hned → všechny kontroly paralelně na pozadí s nízkou prioritou.
2. Nálezy se porovnají se stavem (deduplikace). Nic nového → ikona klidná, konec, bez tokenů.
3. Je-li něco nového: když je Claude dostupný, jedno volání: vstup = seznam nálezů (bez `items` nad 10 položek, jen počty), text relevantních novinek Arch, výstup = JSON `{headline, body, risks[]}` v češtině, max 600 znaků. Jinak šablona.
4. Notifikace Plasmy: titulek = headline, text = body + první akce. Tlačítka: **Ano**, **Ano, příště automaticky**, **Ne**. Při více nálezích jedna souhrnná notifikace s tlačítkem „Otevřít nabídku“ a jednotlivé akce v nabídce ikony.
5. Akce s režimem `auto` se spustí rovnou (kromě výjimky `arch-news`), ostatní po kliknutí. Během běhu ikona se stavem „běží“.
6. Po každé akci popup s výsledkem (stdout akce), log s plným výstupem. Po `update` navíc kontrola `secure-boot` a `reboot-needed`, výsledek v tomtéž popupu.
7. Opakování každých 6 h; ruční „Zkontrolovat teď“ v nabídce; nabídka dál obsahuje čekající akce, „Otevřít log“, „Nastavení“ (otevře `config.toml` v editoru), „Ukončit“.

## 10. Bezpečnost

- Vše, co maže, maže jen to, co kontrola vyjmenovala; akce dostane konkrétní argumenty, nehledá znovu sama.
- Nikdy automaticky: `remove-package`, cokoli v `docker-vm`, system jednotky, `pacdiff`.
- `arch-news` s „manual intervention“ pro čekající balíček přepne `update` na dotaz, i když je `auto`.
- Režim `auto` u `update` platí i pro jádro, systemd a Plasmu (po jádře následuje kontrola `secure-boot`). **Výjimka: `qemu*` / `libvirt` v dávce, když běží nějaká VM (`virsh list --state-running`), vynutí dotaz.** Schváleno s návrhem 2026-10-17.
- Klíč API se čte ze `secrets.zsh` (§ 5), do konfigurace ani logu se neukládá. Do Claude se nikdy neposílají cesty v `/home` mimo názvy adresářů IDE, ani obsah souborů.
- Příkazy pod `sudo` se volají s absolutní cestou a pevnými argumenty, aby šlo později přejít na úzký seznam v sudoers bez změny kódu.

## 11. Co je na staviteli

- Struktura repa, pojmenování modulů, testy (kontroly a akce testovat na kopii stromu adresářů a s nahrazenými příkazy; GUI ručně).
- Přesný vzhled nabídky a formát popupů (v rámci § 9).
- Implementace baseline pro `resource-hogs` a heuristika `unused-apps`.
- Instalační skript a jeho idempotence.
- Kreslení ikony (bílá značka Anthropic, 24 px).
- Pořadí implementace: doporučeno nejdřív jádro (runner, tray, notifikace, jedna kontrola `updates` + akce `update`), pak zbytek kontrol po jedné, Claude shrnutí až na konci, protože bez něj to funguje.

## 12. Napojení na `custom-arch-setup-script` (později)

Uživatel chce, aby se asistent instaloval jako součást jeho instalačního skriptu stroje (`~/projects/custom-arch-setup-script`, `post-install.sh`). Není to součást prvního kroku: `install.sh` v tomto repu musí být idempotentní a spustitelný bez interakce (kromě `sudo`), aby ho `post-install.sh` později jen zavolal (naklonovat repo, spustit `install.sh`, odinstalovat `octopi`). Ikona tray zůstane v tomto repu, ne v motivu Simpleton.
