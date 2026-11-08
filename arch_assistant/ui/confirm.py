"""Confirmations before packages are installed or removed, and the explanation when a removal is refused."""
from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtWidgets import QAbstractButton, QMessageBox, QWidget

from ..packages import SOURCE_LABELS
from ..protection import RemovalPlan

MAX_CONFIRM_LINES = 15
UNINSTALL_NOTE = ("Packages are removed with pacman -Rns, together with the dependencies nothing else needs "
                  "(marked above). Flatpak app data in ~/.var/app is kept.")
INSTALL_NOTE = ("Arch packages come from the official repos, Flatpaks from Flathub (system-wide). "
                "AUR packages are made by users and built from their PKGBUILD on this machine.")
REFUSED_NOTE = ("Nothing was removed. Protected packages, the login shell and what Arch Assistant needs never "
                "go from here; use a terminal if you really mean it.")


def _line(name: str, package_id: str, source: str) -> str:
    return f"• {name}" + (f" ({package_id})" if name != package_id else "") + f" – {SOURCE_LABELS.get(source, source)}"


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _box(verb: str, text: str, lines: list[str], note: str, parent: QWidget | None) -> tuple[QMessageBox, QAbstractButton]:
    """A confirmation listing `lines` (the first MAX_CONFIRM_LINES, all of them under Details)."""
    shown = lines[:MAX_CONFIRM_LINES] + ([f"… and {len(lines) - MAX_CONFIRM_LINES} more"]
                                         if len(lines) > MAX_CONFIRM_LINES else [])
    destructive = verb == "Uninstall"
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question)
    box.setWindowTitle(f"Arch Assistant – {verb.lower()}")
    box.setText(text)
    box.setInformativeText("\n".join(shown) + f"\n\n{note}")
    if len(lines) > MAX_CONFIRM_LINES:
        box.setDetailedText("\n".join(lines))
    yes = box.addButton(verb, QMessageBox.ButtonRole.DestructiveRole if destructive else QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box, yes


def _ask(box: QMessageBox, yes: QAbstractButton) -> bool:
    box.exec()
    return box.clickedButton() is yes


def install_box(rows: list, parent: QWidget | None = None) -> tuple[QMessageBox, QAbstractButton]:
    """Rows have name, id and source (catalog.Result)."""
    return _box("Install", f"Install {_count(len(rows), 'package', 'packages')}?",
                [_line(r.name, r.id, r.source) for r in rows], INSTALL_NOTE, parent)


def confirm_install(rows: list, parent: QWidget | None = None) -> bool:
    return _ask(*install_box(rows, parent))


def removal_box(plan: RemovalPlan, known: Mapping[str, object],
                parent: QWidget | None = None) -> tuple[QMessageBox, QAbstractButton]:
    """Everything the removal takes: the picked packages and Flatpaks (names from `known`, id → Installed row),
    then every dependency pacman removes with them, marked as such."""
    def picked(package_id: str, source: str) -> str:
        row = known.get(package_id)
        return _line(row.name, row.id, row.source) if row is not None else _line(package_id, package_id, source)

    lines = [picked(name, source) for source, name in plan.picked] + [picked(f, "flatpak") for f in plan.flatpaks]
    deps = plan.dependencies
    if deps:
        lines += ["", "Dependencies nothing else needs, removed with them:", *(f"• {d} (dependency)" for d in deps)]
    text = f"Uninstall {_count(len(plan.picked) + len(plan.flatpaks), 'package', 'packages')}"
    text += f" and {_count(len(deps), 'dependency', 'dependencies')}?" if deps else "?"
    return _box("Uninstall", text, lines, UNINSTALL_NOTE, parent)


def confirm_removal(plan: RemovalPlan, known: Mapping[str, object], parent: QWidget | None = None) -> bool:
    return _ask(*removal_box(plan, known, parent))


def refusal_box(problem: str, plan: RemovalPlan | None, parent: QWidget | None = None) -> QMessageBox:
    """Why an uninstall did not even start: a blocked package in the removal set, or pacman refusing it."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle("Arch Assistant – uninstall refused")
    box.setText("This uninstall cannot run.")
    box.setStandardButtons(QMessageBox.StandardButton.Close)
    if plan is not None and plan.blocked:
        why = "\n".join(f"• {p} – {reason}" for p, reason in plan.blocked)
        box.setInformativeText(f"The removal would also take packages that must stay:\n{why}\n\n{REFUSED_NOTE}")
    else:
        box.setInformativeText(f"{problem}\n\n{REFUSED_NOTE}")
    if plan is not None and plan.packages:
        box.setDetailedText("pacman -Rns would remove:\n" + "\n".join(plan.packages))
    return box


def show_refusal(problem: str, plan: RemovalPlan | None, parent: QWidget | None = None) -> None:
    refusal_box(problem, plan, parent).exec()
