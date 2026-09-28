"""
gui/math_dialog.py
------------------
Modal editor for a math channel ("Canal matemático"): pick which trace is
bound to each alias (A..D), write the operation, name the result and choose
its magnitude. Pure orchestration -- parsing/evaluation live in
`core/math_channels.py`; the caller passes `validate` to run a real
evaluation before the dialog closes.

Layout rules (see Claude.md §3): no fixed pixel sizes, no nested scrollable
frames, grid columns with `weight=1` so every row stretches with the window,
and a static `wraplength` on hints (never recomputed on <Configure>).
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

import customtkinter as ctk

from core.i18n import t
from core.math_channels import (
    ALIASES, FUNCTION_NAMES, PRESETS, MathError, MathSpec, describe,
    referenced_aliases,
)
from gui.theme import col, font
from gui.widgets import LabeledCombo, ghost_button, hint, primary_button

_NONE = "—"
Y_KINDS = ["voltage", "dB", "deg", "custom"]


def _y_kind_labels() -> dict:
    return {"voltage": t("Tensión"), "dB": "dB", "deg": t("Fase"),
            "custom": t("Otra (unidad libre)")}


class MathChannelDialog(ctk.CTkToplevel):
    """
    `candidates`: (uid, visible name) of every trace that can be an operand
    (the caller already excludes the channel being edited and anything that
    depends on it). `initial`: an existing MathSpec when editing.
    `validate(spec) -> Optional[str]`: error text, or None if it evaluates.
    After `wait_window`, `self.result` is a MathSpec or None (cancelled).
    """

    def __init__(self, master, candidates: Sequence[tuple[str, str]],
                 initial: Optional[MathSpec] = None,
                 validate: Optional[Callable[[MathSpec], Optional[str]]] = None,
                 default_kind: Callable[[str], str] = lambda _uid: "voltage"):
        super().__init__(master)
        self.title(t("Editar canal matemático") if initial else t("Nuevo canal matemático"))
        self.resizable(True, True)
        self.result: Optional[MathSpec] = None
        self._validate = validate
        self._default_kind = default_kind

        # Unique display labels (two traces may share a name).
        self._label_to_uid: dict[str, str] = {}
        self._uid_to_label: dict[str, str] = {}
        for uid, name in candidates:
            label, n = name, 2
            while label in self._label_to_uid:
                label, n = f"{name} ({n})", n + 1
            self._label_to_uid[label] = uid
            self._uid_to_label[uid] = label
        self._name_touched = bool(initial)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=16)
        body.grid_columnconfigure(1, weight=1)
        row = 0

        hint(body, t("Combiná trazas con una operación, como el MATH de un "
                     "osciloscopio. El resultado se recalcula solo cuando "
                     "cambian las trazas de origen."),
             wraplength=420).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        row += 1

        # ---- channel bindings ------------------------------------------- #
        ctk.CTkLabel(body, text=t("Canales"), font=font("header"),
                     text_color=col("fg_muted"), anchor="w"
                     ).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        row += 1
        values = [_NONE] + list(self._label_to_uid)
        self.alias_vars: dict[str, ctk.StringVar] = {}
        first_uids = [uid for uid, _ in candidates]
        for i, alias in enumerate(ALIASES):
            if initial is not None:
                uid = initial.operands.get(alias)
            else:
                uid = first_uids[i] if i < min(2, len(first_uids)) else None
            var = ctk.StringVar(value=self._uid_to_label.get(uid, _NONE))
            self.alias_vars[alias] = var
            ctk.CTkLabel(body, text=alias, font=font("mono"), text_color=col("fg"),
                         anchor="w").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=2)
            ctk.CTkComboBox(body, values=values, variable=var, height=26,
                            font=font("body"), dropdown_font=font("body"),
                            state="readonly",
                            command=lambda _v: self._on_change()
                            ).grid(row=row, column=1, sticky="ew", pady=2)
            row += 1

        # ---- expression -------------------------------------------------- #
        ctk.CTkLabel(body, text=t("Operación"), font=font("header"),
                     text_color=col("fg_muted"), anchor="w"
                     ).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        row += 1
        self.expr_var = ctk.StringVar(value=initial.expr if initial else "A - B")
        self.expr_entry = ctk.CTkEntry(body, textvariable=self.expr_var, height=30,
                                       font=font("mono"))
        self.expr_entry.grid(row=row, column=0, columnspan=2, sticky="ew")
        self.expr_entry.bind("<KeyRelease>", lambda _e: self._on_change())
        self.expr_entry.bind("<Return>", lambda _e: self._accept())
        row += 1

        presets = ctk.CTkFrame(body, fg_color="transparent")
        presets.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        per_row = 4
        for c in range(per_row):
            presets.grid_columnconfigure(c, weight=1, uniform="preset")
        for i, (label, expr) in enumerate(PRESETS):
            text = t(label) if label.startswith("Suavizar") else label
            ghost_button(presets, text, lambda e=expr: self._use_preset(e), height=26
                         ).grid(row=i // per_row, column=i % per_row, sticky="ew",
                                padx=2, pady=2)
        row += 1

        hint(body, t("Funciones: {fns}. Constantes: pi, e; «t» es el eje X "
                     "(tiempo o frecuencia). Se aceptan prefijos SI: 1k, 10m, 4.7u. "
                     "Decimales con punto.").format(fns=", ".join(FUNCTION_NAMES)),
             wraplength=420).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        row += 1

        self.status = ctk.CTkLabel(body, text="", font=font("small"),
                                   text_color=col("fg_muted"), anchor="w",
                                   justify="left", wraplength=420)
        self.status.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        row += 1

        # ---- result ------------------------------------------------------ #
        ctk.CTkLabel(body, text=t("Resultado"), font=font("header"),
                     text_color=col("fg_muted"), anchor="w"
                     ).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        row += 1
        ctk.CTkLabel(body, text=t("Nombre"), font=font("label"),
                     text_color=col("fg_muted"), anchor="w"
                     ).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=2)
        self.name_var = ctk.StringVar(value=initial.name if initial else "")
        name_entry = ctk.CTkEntry(body, textvariable=self.name_var, height=26, font=font("body"))
        name_entry.grid(row=row, column=1, sticky="ew", pady=2)
        name_entry.bind("<Key>", lambda _e: setattr(self, "_name_touched", True))
        row += 1

        ctk.CTkLabel(body, text=t("Magnitud"), font=font("label"),
                     text_color=col("fg_muted"), anchor="w"
                     ).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=2)
        start_kind = initial.y_kind if initial else self._suggest_kind()
        self.kind_var = ctk.StringVar(value=start_kind)
        LabeledCombo(body, Y_KINDS, self.kind_var, labels=_y_kind_labels(),
                     command=lambda _v: self._sync_unit_state()
                     ).grid(row=row, column=1, sticky="ew", pady=2)
        row += 1

        ctk.CTkLabel(body, text=t("Unidad"), font=font("label"),
                     text_color=col("fg_muted"), anchor="w"
                     ).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=2)
        self.unit_var = ctk.StringVar(value=initial.unit if initial else "")
        self.unit_entry = ctk.CTkEntry(body, textvariable=self.unit_var, height=26,
                                       font=font("body"),
                                       placeholder_text=t("ej.: W, A, V²"))
        self.unit_entry.grid(row=row, column=1, sticky="ew", pady=2)
        row += 1

        # ---- actions ----------------------------------------------------- #
        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        primary_button(actions, t("Guardar cambios") if initial else t("Crear canal"),
                       self._accept, height=30).pack(side="right")
        ghost_button(actions, t("Cancelar"), self._cancel, height=30
                     ).pack(side="right", padx=(0, 8))

        self._sync_unit_state()
        self._on_change()

        self.transient(master)
        self.grab_set()
        self.expr_entry.focus_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window(self)

    # ------------------------------------------------------------------ #
    def _operands(self) -> dict[str, str]:
        out = {}
        for alias, var in self.alias_vars.items():
            uid = self._label_to_uid.get(var.get())
            if uid:
                out[alias] = uid
        return out

    def _suggest_kind(self) -> str:
        uid = self._label_to_uid.get(self.alias_vars["A"].get())
        return self._default_kind(uid) if uid else "voltage"

    def _auto_name(self) -> str:
        names = {a: v.get() for a, v in self.alias_vars.items() if v.get() != _NONE}
        return describe(self.expr_var.get(), names)

    def _use_preset(self, expr: str) -> None:
        self.expr_var.set(expr)
        self._on_change()
        self.expr_entry.focus_set()
        self.expr_entry.icursor("end")

    def _sync_unit_state(self) -> None:
        custom = self.kind_var.get() == "custom"
        self.unit_entry.configure(state="normal" if custom else "disabled")

    def _on_change(self) -> None:
        if not self._name_touched:
            self.name_var.set(self._auto_name())
        try:
            used = referenced_aliases(self.expr_var.get())
        except MathError as exc:
            self.status.configure(text=f"⚠ {exc}")
            return
        if not used:
            self.status.configure(text="⚠ " + t("La operación tiene que usar al menos "
                                               "un canal (A, B, C o D)."))
            return
        ops = self._operands()
        unbound = [a for a in used if a not in ops]
        if unbound:
            self.status.configure(text="⚠ " + t("El canal {alias} no está asignado a "
                                               "ninguna traza.").format(alias=unbound[0]))
            return
        self.status.configure(text="✓ " + t("Usa: {aliases}").format(aliases=", ".join(used)))

    def _spec(self) -> MathSpec:
        kind = self.kind_var.get() if self.kind_var.get() in Y_KINDS else "voltage"
        ops = self._operands()
        try:
            used = set(referenced_aliases(self.expr_var.get()))
            ops = {a: u for a, u in ops.items() if a in used}
        except MathError:
            pass
        return MathSpec(expr=self.expr_var.get().strip(), operands=ops,
                        name=self.name_var.get().strip() or self._auto_name(),
                        y_kind=kind,
                        unit=self.unit_var.get().strip() if kind == "custom" else "")

    def _accept(self) -> None:
        spec = self._spec()
        error = self._validate(spec) if self._validate else None
        if error:
            self.status.configure(text=f"⚠ {error}")
            return
        self.result = spec
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()
