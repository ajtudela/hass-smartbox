/**
 * Smartbox program card.
 *
 * A dependency-free Lovelace card for a heater's weekly program. The
 * integration serves this module from `/smartbox_frontend/` and loads it as an
 * extra frontend module, so the card needs no Lovelace resource.
 *
 * Data contract, all on the heater's schedule sensor:
 * - `attributes.schedule`: `{"0": [...], ..., "6": [...]}`, day keys
 *   Sunday..Saturday (HA's weekday index, matching the wire), one profile
 *   index per slot.
 * - `attributes.options`: profile names in wire-index order (the enum
 *   sensor's capability attribute); `frost`/`eco`/`comfort` for the plain
 *   heater family.
 * - `attributes.resolution` / `attributes.resolution_available`: the current
 *   slot resolution (0 hourly, 1 half-hourly) and whether the editor may
 *   toggle it.
 * - `smartbox.set_schedule`: the write path; only changed days are sent, and
 *   the service merges them into the current week.
 * - `smartbox.set_schedule_resolution`: writes the editor resolution.
 *
 * The card presents the plan in a seven-column week grid (days across, time
 * slots down). Columns follow the Home Assistant user's first-day-of-week
 * setting, while day keys remain the data keys. Mode, presets and override
 * state belong to the climate entity, so the card does not duplicate them.
 * The card shell is the native
 * `ha-card`; the chart and legend are custom because HA has no schedule-grid
 * component. The preview merges equal neighbouring slots into bands and
 * carries a 00/06/12/18 time axis; the legend option hides the profile
 * legend. The editor keeps the per-slot
 * grid with the same header, gutter, hour-guide and rounded-corner
 * treatment. Profile fills are mixed with the card surface so they follow
 * the theme. The card is configured with the heater's schedule sensor
 * entity. The config editor uses `ha-form` and the programme editor uses the
 * native `ha-dialog`; closing a changed draft opens a native alert dialog
 * before the edits are discarded.
 *
 * The card only reads state from `hass.states` and writes through
 * `hass.callService`; it holds no device connection of its own.
 */

const CARD_TYPE = "smartbox-program-card";
const EDITOR_TYPE = "smartbox-program-card-editor";
let nextEditorHintId = 0;

const DAY_KEYS = ["0", "1", "2", "3", "4", "5", "6"];
const FALLBACK_PROFILES = ["frost", "eco", "comfort"];
// The fill is mixed with the card surface so the bands follow the active
// theme. The --smartbox-* variables let a theme replace a fill outright.
const PROFILE_COLORS = {
  frost:
    "var(--smartbox-frost-color, color-mix(in srgb, #4f8ff7 85%, var(--card-background-color, #1c1c1c)))",
  eco:
    "var(--smartbox-eco-color, color-mix(in srgb, #2f9e63 85%, var(--card-background-color, #1c1c1c)))",
  comfort:
    "var(--smartbox-comfort-color, color-mix(in srgb, #e8862e 85%, var(--card-background-color, #1c1c1c)))",
  off:
    "var(--smartbox-off-color, color-mix(in srgb, #8a8f94 85%, var(--card-background-color, #1c1c1c)))",
};
const UNKNOWN_COLOR = "#9aa0a6";
const MINUTES_PER_DAY = 1440;
const MAX_SLOTS = 96;
const WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAYS_LONG = [
  "sunday",
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
];
const TEMP_KEYS = {
  frost: "prog_frost_temperature",
  eco: "prog_eco_temperature",
  comfort: "prog_comfort_temperature",
};
const SAVE_SETTLE_MS = 8000;

// Home Assistant's WeekdayIndex is Sunday=0..Saturday=6. Mirror its
// firstWeekdayIndex(): an explicit profile choice wins; "language" resolves
// through Intl.Locale.weekInfo, falling back to Monday when unavailable.
function firstWeekdayIndex(locale) {
  const setting = locale?.first_weekday;
  if (setting && setting !== "language") {
    const index = WEEKDAYS_LONG.indexOf(setting);
    return index >= 0 ? index : 1;
  }
  const language = locale?.language;
  if (
    language &&
    typeof Intl !== "undefined" &&
    typeof Intl.Locale === "function"
  ) {
    try {
      const intlLocale = new Intl.Locale(language);
      const info = intlLocale.weekInfo ?? intlLocale.getWeekInfo?.();
      if (info && Number.isInteger(info.firstDay)) {
        return info.firstDay % 7;
      }
    } catch {
      // Fall through to the Monday default.
    }
  }
  return 1;
}

function daysFromFirstWeekday(first) {
  return Array.from({ length: 7 }, (_, index) => (first + index) % 7);
}

const HTML_ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

const STRINGS = {
  en: {
    program: "Program",
    edit: "Edit program",
    apply: "Apply",
    cancel: "Cancel",
    undo: "Undo",
    reset: "Reset",
    cycle: "Cycle",
    frost: "Frost",
    eco: "Eco",
    off: "Off",
    comfort: "Comfort",
    unknown: "Unknown",
    noSchedule: "The heater has not reported a program yet.",
    unsupported:
      "This heater reports a program format this card does not support.",
    unavailable: "The heater is unavailable.",
    chooseEntity: "Choose a Smartbox heater's Schedule sensor.",
    missingEntity: "The configured entity is not available.",
    notSmartbox: "This card needs a Smartbox Schedule sensor.",
    sent: "Program sent.",
    unconfirmed: "Program sent, but the heater has not confirmed it.",
    sending: "Sending…",
    saveFailed: "Could not save the program.",
    changedElsewhere: "The program changed on the heater.",
    reload: "Reload",
    keepMine: "Keep mine",
    discardPrompt: "Discard unsaved changes?",
    discardText: "Unsaved changes will be lost.",
    discard: "Discard",
    copyTo: "Copy from {day} to",
    daySource: "Use {day} as the copy source",
    instructions:
      "Drag with a mouse to paint. Tap a cell to apply the selected profile. Tap a day header to choose the copy source.",
    weekdays: "Weekdays",
    weekend: "Weekend",
    allDays: "All days",
    editHintUnsupported: "Editing is disabled for this program.",
    editHintNoSchedule: "Waiting for the heater to report a program.",
    editHintUnavailable: "The heater is unavailable.",
    configTitle: "Title",
    configEntity: "Schedule sensor",
    configLegend: "Legend",
    resolution: "Resolution",
    hourly: "Hourly",
    halfHourly: "Half-hourly",
  },
  de: {
    program: "Programm",
    edit: "Programm bearbeiten",
    apply: "Übernehmen",
    cancel: "Abbrechen",
    undo: "Rückgängig",
    reset: "Zurücksetzen",
    cycle: "Wechseln",
    frost: "Frost",
    eco: "Eco",
    off: "Aus",
    comfort: "Komfort",
    unknown: "Unbekannt",
    noSchedule: "Die Heizung hat noch kein Programm gemeldet.",
    unsupported:
      "Dieses Programmformat wird von der Karte nicht unterstützt.",
    unavailable: "Die Heizung ist nicht verfügbar.",
    chooseEntity: "Wähle den Schedule-Sensor einer Smartbox-Heizung.",
    missingEntity: "Die konfigurierte Entität ist nicht verfügbar.",
    notSmartbox: "Diese Karte benötigt einen Smartbox-Schedule-Sensor.",
    sent: "Programm gesendet.",
    unconfirmed:
      "Programm gesendet, aber die Heizung hat es nicht bestätigt.",
    sending: "Senden…",
    saveFailed: "Das Programm konnte nicht gespeichert werden.",
    changedElsewhere: "Das Programm wurde auf der Heizung geändert.",
    reload: "Neu laden",
    keepMine: "Meins behalten",
    discardPrompt: "Nicht gespeicherte Änderungen verwerfen?",
    discardText: "Nicht gespeicherte Änderungen gehen verloren.",
    discard: "Verwerfen",
    copyTo: "Von {day} kopieren nach",
    daySource: "Verwende {day} als Kopierquelle",
    instructions:
      "Mit der Maus ziehen zum Malen. Tippe auf eine Zelle, um das Profil anzuwenden. Tippe auf eine Tagesüberschrift, um die Kopierquelle zu wählen.",
    weekdays: "Werktage",
    weekend: "Wochenende",
    allDays: "Alle Tage",
    editHintUnsupported: "Bearbeiten ist für dieses Programm deaktiviert.",
    editHintNoSchedule: "Warte auf ein Programm von der Heizung.",
    editHintUnavailable: "Die Heizung ist nicht verfügbar.",
    configTitle: "Titel",
    configEntity: "Schedule-Sensor",
    configLegend: "Legende",
    resolution: "Auflösung",
    hourly: "Stündlich",
    halfHourly: "Halbstündlich",
  },
  es: {
    program: "Programa",
    edit: "Editar programa",
    apply: "Aplicar",
    cancel: "Cancelar",
    undo: "Deshacer",
    reset: "Restablecer",
    cycle: "Alternar",
    frost: "Escarcha",
    eco: "Eco",
    off: "Apagado",
    comfort: "Confort",
    unknown: "Desconocido",
    noSchedule: "El calefactor aún no ha comunicado un programa.",
    unsupported:
      "Este formato de programa no es compatible con la tarjeta.",
    unavailable: "El calefactor no está disponible.",
    chooseEntity: "Elige el sensor de programa de un calefactor Smartbox.",
    missingEntity: "La entidad configurada no está disponible.",
    notSmartbox: "Esta tarjeta necesita un sensor de programa Smartbox.",
    sent: "Programa enviado.",
    unconfirmed:
      "Programa enviado, pero el calefactor no lo ha confirmado.",
    sending: "Enviando…",
    saveFailed: "No se pudo guardar el programa.",
    changedElsewhere: "El programa cambió en el calefactor.",
    reload: "Recargar",
    keepMine: "Mantener el mío",
    discardPrompt: "¿Descartar los cambios sin guardar?",
    discardText: "Se perderán los cambios sin guardar.",
    discard: "Descartar",
    copyTo: "Copiar desde {day} a",
    daySource: "Usar {day} como origen de la copia",
    instructions:
      "Arrastra con el ratón para pintar. Toca una celda para aplicar el perfil. Toca la cabecera de un día para elegir el origen de la copia.",
    weekdays: "Entre semana",
    weekend: "Fin de semana",
    allDays: "Todos los días",
    editHintUnsupported: "La edición está desactivada para este programa.",
    editHintNoSchedule: "Esperando a que el calefactor comunique un programa.",
    editHintUnavailable: "El calefactor no está disponible.",
    configTitle: "Título",
    configEntity: "Sensor de programa",
    configLegend: "Leyenda",
    resolution: "Resolución",
    hourly: "Horaria",
    halfHourly: "Media horaria",
  },
  fr: {
    program: "Programme",
    edit: "Modifier le programme",
    apply: "Appliquer",
    cancel: "Annuler",
    undo: "Défaire",
    reset: "Réinitialiser",
    cycle: "Alterner",
    frost: "Gel",
    eco: "Éco",
    off: "Arrêt",
    comfort: "Confort",
    unknown: "Inconnu",
    noSchedule: "Le radiateur n'a pas encore signalé de programme.",
    unsupported:
      "Ce format de programme n'est pas pris en charge par la carte.",
    unavailable: "Le radiateur est indisponible.",
    chooseEntity:
      "Choisissez le capteur de programme d'un radiateur Smartbox.",
    missingEntity: "L'entité configurée n'est pas disponible.",
    notSmartbox: "Cette carte nécessite un capteur de programme Smartbox.",
    sent: "Programme envoyé.",
    unconfirmed:
      "Programme envoyé, mais le radiateur ne l'a pas confirmé.",
    sending: "Envoi…",
    saveFailed: "Impossible d'enregistrer le programme.",
    changedElsewhere: "Le programme a changé sur le radiateur.",
    reload: "Recharger",
    keepMine: "Garder le mien",
    discardPrompt: "Abandonner les modifications non enregistrées ?",
    discardText: "Les modifications non enregistrées seront perdues.",
    discard: "Abandonner",
    copyTo: "Copier de {day} vers",
    daySource: "Utiliser {day} comme source de la copie",
    instructions:
      "Faites glisser à la souris pour peindre. Touchez une cellule pour appliquer le profil. Touchez l'en-tête d'un jour pour choisir la source de la copie.",
    weekdays: "Jours ouvrés",
    weekend: "Week-end",
    allDays: "Tous les jours",
    editHintUnsupported: "La modification est désactivée pour ce programme.",
    editHintNoSchedule: "En attente d'un programme du radiateur.",
    editHintUnavailable: "Le radiateur est indisponible.",
    configTitle: "Titre",
    configEntity: "Capteur de programme",
    configLegend: "Légende",
    resolution: "Résolution",
    hourly: "Horaire",
    halfHourly: "Demi-horaire",
  },
};

const CARD_CSS = `
  :host { display: block; }
  * { box-sizing: border-box; }
  .card-content {
    display: flex;
    flex-direction: column;
    gap: 12px;
    min-width: 0;
    font-family: var(--ha-font-family-body, var(--paper-font-body1_-_font-family, inherit));
  }
  .header { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
  .titles { min-width: 0; }
  .title {
    font-size: var(--ha-font-size-xl, 20px);
    font-weight: var(--ha-font-weight-medium, 500);
    line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .card-content:focus { outline: none; }
  .card-content:focus-visible { outline: 2px solid var(--primary-color, #03a9f4); outline-offset: 2px; }
  .preview-button {
    position: relative; border-radius: 8px; outline: none;
    cursor: pointer; -webkit-tap-highlight-color: transparent;
  }
  .preview-button:not([role="button"]) { cursor: default; }
  .preview-button:focus-visible { outline: 2px solid var(--primary-color, #03a9f4); outline-offset: 2px; }
  .preview-edit {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    gap: 6px; border-radius: 8px; background: rgba(0, 0, 0, 0.46);
    color: #fff; font-size: var(--ha-font-size-m, 14px);
    font-weight: var(--ha-font-weight-medium, 500);
    opacity: 0; pointer-events: none;
    transition: opacity 0.15s ease;
  }
  .preview-edit ha-icon { --mdc-icon-size: 18px; color: inherit; }
  .preview-button:hover .preview-edit,
  .preview-button:focus-visible .preview-edit { opacity: 1; }
  .preview {
    display: grid; gap: 0;
    background: transparent;
  }
  .preview .day, .preview-corner {
    height: 18px; font-size: var(--ha-font-size-s, 12px); line-height: 1;
    font-weight: var(--ha-font-weight-medium, 500);
    color: var(--secondary-text-color);
    background: var(--card-background-color, var(--primary-background-color, #1c1c1c));
    display: flex; align-items: center; justify-content: center;
  }
  .preview .cell.day-start { border-left: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12)); }
  .preview .cell.hour-line { border-top: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12)); }
  .preview .corner-tl { border-top-left-radius: var(--preview-corner-radius, 8px); }
  .preview .corner-tr { border-top-right-radius: var(--preview-corner-radius, 8px); }
  .preview .corner-bl { border-bottom-left-radius: var(--preview-corner-radius, 8px); }
  .preview .corner-br { border-bottom-right-radius: var(--preview-corner-radius, 8px); }
  .preview-time {
    display: flex; align-items: center; justify-content: flex-end;
    padding-right: 6px; font-size: var(--ha-font-size-xs, 10px);
    line-height: 1; color: var(--secondary-text-color);
    background: var(--card-background-color, var(--primary-background-color, #1c1c1c));
    font-variant-numeric: tabular-nums;
  }
  .preview .cell {
    height: var(--preview-cell-height, 12px); min-height: 0; min-width: 0;
    cursor: pointer; -webkit-tap-highlight-color: transparent;
  }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 12px; }
  .legend-chip {
    display: inline-flex; align-items: center; gap: 4px; padding: 2px 6px; margin: -2px -6px;
    border: 0; border-radius: 999px;
    background: none; color: var(--secondary-text-color); font: inherit;
    font-size: var(--ha-font-size-m, 14px); cursor: default;
    -webkit-tap-highlight-color: transparent;
  }
  button.legend-chip { cursor: pointer; }
  button.legend-chip:hover { color: var(--primary-text-color); }
  .dot { width: 10px; height: 10px; border-radius: 3px; display: inline-block; flex: none; }
  .legend-chip .temp { color: var(--primary-text-color); }
  .banner {
    display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
    padding: 10px 12px; border-radius: 8px; font-size: var(--ha-font-size-m, 14px);
    border-left: 3px solid var(--warning-color, #ff9800);
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.04));
  }
  .banner.error { border-left-color: var(--error-color, #db4437); color: var(--error-color, #db4437); }
  .banner.info { border-left-color: var(--primary-color, #03a9f4); }
  .banner.success { border-left-color: #2f9e63; }
  .banner-actions { display: inline-flex; gap: 6px; margin-left: auto; }
  .banner .btn.small { background: var(--ha-card-background, var(--card-background-color, #fff)); }
  .card-status { display: flex; flex-direction: column; gap: 6px; }
  .card-status:empty { display: none; }
  .btn {
    font: inherit; font-size: var(--ha-font-size-m, 14px);
    min-height: 40px; padding: 0 16px; border-radius: 999px;
    display: inline-flex; align-items: center; justify-content: center;
    border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.2));
    background: var(--card-background-color, #fff); color: var(--primary-text-color);
    cursor: pointer; -webkit-tap-highlight-color: transparent;
    transition: background-color 0.15s ease, filter 0.15s ease, transform 0.08s ease;
  }
  .btn:hover { filter: brightness(1.08); }
  .btn:active { transform: translateY(1px); }
  .btn:focus-visible { outline: 2px solid var(--primary-color, #03a9f4); outline-offset: 2px; }
  .btn.small {
    font-size: var(--ha-font-size-s, 12px);
    min-height: 32px; padding: 0 12px; border-radius: 8px;
    border-color: transparent;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
  }
  .btn.small:hover { background: var(--divider-color); filter: none; }
  .btn:disabled { opacity: 0.45; cursor: default; }
  .hint { font-size: var(--ha-font-size-s, 12px); color: var(--secondary-text-color); }
  .unavailable { font-size: var(--ha-font-size-m, 14px); color: var(--secondary-text-color); }

  .dialog-content { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  .dialog-banners { display: flex; flex-direction: column; gap: 6px; }
  .dialog-banners:empty { display: none; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
  .brushes { display: flex; flex-wrap: wrap; gap: 6px; }
  .brush {
    display: inline-flex; align-items: center; gap: 6px; padding: 6px 11px;
    border-radius: 8px; border: 1px solid transparent;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
    color: var(--primary-text-color);
    font: inherit; font-size: var(--ha-font-size-s, 12px); cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: background-color 0.15s ease, box-shadow 0.15s ease;
  }
  .brush:hover { background: var(--divider-color); }
  .brush .temp { color: var(--secondary-text-color); font-size: var(--ha-font-size-xs, 10px); }
  .brush.selected {
    background: rgba(3, 169, 244, 0.16);
    background: color-mix(in srgb, var(--primary-color, #03a9f4) 18%, transparent);
    color: var(--primary-color, #03a9f4);
    box-shadow: inset 0 0 0 1px var(--primary-color, #03a9f4);
  }
  .brush.selected .temp { color: var(--primary-color, #03a9f4); opacity: 0.8; }
  .brush:focus-visible { outline: 2px solid var(--primary-color, #03a9f4); outline-offset: 2px; }
  .tools { display: flex; gap: 6px; }
  .copy-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .copy-label { font-size: var(--ha-font-size-s, 12px); color: var(--secondary-text-color); margin-right: 2px; }
  .grid-frame {
    min-width: 0; width: 100%;
    background: transparent;
  }
  .grid {
    display: grid; gap: 0; width: max-content; min-width: 100%;
    grid-template-columns: 54px repeat(7, minmax(42px, 1fr));
    background: transparent;
  }
  .grid-row { display: contents; }
  .grid-corner {
    position: sticky; top: 0; left: 0; z-index: 3; height: 30px;
    background: var(--card-background-color, var(--primary-background-color, #1c1c1c));
  }
  .grid .day-label {
    position: sticky; top: 0; z-index: 2; height: 30px;
    text-align: center; padding: 0 4px; border: 0; font: inherit;
    font-size: var(--ha-font-size-s, 12px); font-weight: var(--ha-font-weight-medium, 500);
    color: var(--primary-text-color);
    background: var(--card-background-color, var(--primary-background-color, #1c1c1c));
    cursor: pointer; white-space: nowrap;
    -webkit-tap-highlight-color: transparent;
  }
  .grid .day-label:hover { background: var(--secondary-background-color, rgba(0, 0, 0, 0.06)); }
  .grid .day-label.selected {
    color: var(--primary-color, #03a9f4); font-weight: 600;
    box-shadow: inset 0 -2px 0 var(--primary-color, #03a9f4);
  }
  .grid .day-label:focus-visible { outline: 2px solid var(--primary-color, #03a9f4); outline-offset: -2px; z-index: 3; }
  .slot-time {
    position: sticky; left: 0; z-index: 1; height: 30px;
    display: flex; align-items: center; justify-content: flex-end;
    padding-right: 6px; font-size: var(--ha-font-size-s, 12px);
    color: var(--secondary-text-color);
    background: var(--card-background-color, var(--primary-background-color, #1c1c1c));
    white-space: nowrap;
  }
  .grid .cell {
    height: 30px; min-width: 0; padding: 0; border: 0; border-radius: 0;
    border-top: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12));
    cursor: pointer; background: ${UNKNOWN_COLOR};
    -webkit-tap-highlight-color: transparent;
    transition: filter 0.1s ease;
  }
  .grid .cell[data-slot="0"] { border-top: 0; }
  .grid .cell.hour-line { border-top-width: 2px; }
  .grid .cell.day-start { border-left: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12)); }
  .grid .cell.corner-tl { border-top-left-radius: 8px; }
  .grid .cell.corner-tr { border-top-right-radius: 8px; }
  .grid .cell.corner-bl { border-bottom-left-radius: 8px; }
  .grid .cell.corner-br { border-bottom-right-radius: 8px; }
  .grid .cell:hover { filter: brightness(1.1); }
  .grid .cell:focus-visible { outline: 2px solid var(--primary-text-color); outline-offset: -2px; z-index: 1; }
  .dialog-footer {
    display: flex; justify-content: flex-end; gap: 8px; width: 100%;
  }
  .confirm-text { margin: 0; color: var(--primary-text-color); }
  @media (max-width: 600px) {
    :host {
      --smartbox-grid-line: color-mix(
        in srgb, var(--primary-text-color, #fff) 40%, transparent
      );
      --smartbox-grid-line-strong: color-mix(
        in srgb, var(--primary-text-color, #fff) 60%, transparent
      );
    }
    .preview .cell.day-start {
      border-left-color: var(--smartbox-grid-line);
    }
    .preview .cell.hour-line {
      border-top-color: var(--smartbox-grid-line);
    }
    .grid { grid-template-columns: 44px repeat(7, minmax(0, 1fr)); }
    .grid .cell {
      height: 34px;
      border-top-width: 2px;
      border-top-color: var(--smartbox-grid-line);
    }
    .grid .cell[data-slot="0"] { border-top-width: 0; }
    .grid .cell.hour-line {
      border-top-width: 3px;
      border-top-color: var(--smartbox-grid-line-strong);
    }
    .grid .cell.day-start {
      border-left-width: 2px;
      border-left-color: var(--smartbox-grid-line);
    }
    .grid .day-label, .grid-corner, .slot-time { height: 34px; }
    .grid .day-label { padding: 0 2px; }
  }
  @media (max-width: 360px) {
    .grid { grid-template-columns: 40px repeat(7, minmax(0, 1fr)); }
    .slot-time {
      padding-right: 4px;
      font-size: var(--ha-font-size-xs, 10px);
    }
  }
  @media (prefers-reduced-motion: no-preference) {
    .cell { transition: filter 0.1s ease; }
  }
`;

const EDITOR_CSS = `
  :host { display: block; }
  .form { padding: 8px 0; }
  ha-form { display: block; }
`;

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (character) => HTML_ESCAPES[character],
  );
}

function stringsFor(language) {
  const base = String(language || "en")
    .split("-")[0]
    .toLowerCase();
  return { ...STRINGS.en, ...(STRINGS[base] || {}) };
}

function legendEnabled(config) {
  return config?.show_legend !== false;
}

function registryEntries(hass) {
  const registry = hass?.entities;
  if (!registry) {
    return [];
  }
  if (registry instanceof Map) {
    return Array.from(registry.values());
  }
  return Object.values(registry);
}

function cloneSchedule(schedule) {
  const clone = {};
  for (const day of DAY_KEYS) {
    const slots = schedule?.[day];
    clone[day] = Array.isArray(slots) ? slots.slice() : [];
  }
  return clone;
}

function profilesFor(state) {
  const options = state?.attributes?.options;
  if (Array.isArray(options)) {
    const named = options.filter(
      (option) => typeof option === "string" && option.length > 0,
    );
    if (named.length) {
      return named;
    }
  }
  return FALLBACK_PROFILES.slice();
}

function scheduleShape(schedule, profileCount) {
  if (!schedule || typeof schedule !== "object" || Array.isArray(schedule)) {
    return { slotCount: 0, editable: false, reason: "none" };
  }
  const days = DAY_KEYS.map((day) => schedule[day]);
  if (days.some((slots) => !Array.isArray(slots) || slots.length === 0)) {
    return { slotCount: 0, editable: false, reason: "shape" };
  }
  const slotCount = days[0].length;
  if (days.some((slots) => slots.length !== slotCount)) {
    return { slotCount, editable: false, reason: "shape" };
  }
  if (
    slotCount > MAX_SLOTS ||
    !Number.isInteger(MINUTES_PER_DAY / slotCount)
  ) {
    return { slotCount, editable: false, reason: "slots" };
  }
  const profilesValid = days.every((slots) =>
    slots.every(
      (value) =>
        Number.isInteger(value) && value >= 0 && value < profileCount,
    ),
  );
  if (!profilesValid) {
    return { slotCount, editable: false, reason: "profiles" };
  }
  return { slotCount, editable: true, reason: "ok" };
}

function slotMinutesFor(slotCount) {
  return slotCount > 0 ? MINUTES_PER_DAY / slotCount : 60;
}

function diffDays(original, draft) {
  const changed = {};
  for (const day of DAY_KEYS) {
    if (JSON.stringify(original?.[day]) !== JSON.stringify(draft?.[day])) {
      changed[day] = Array.isArray(draft?.[day]) ? draft[day].slice() : [];
    }
  }
  return changed;
}

function convertScheduleResolution(schedule, fromSlots, toSlots) {
  if (fromSlots === toSlots) {
    return cloneSchedule(schedule);
  }
  const converted = {};
  for (const day of DAY_KEYS) {
    const slots = Array.isArray(schedule?.[day]) ? schedule[day] : [];
    if (toSlots > fromSlots && fromSlots > 0) {
      const factor = Math.round(toSlots / fromSlots);
      converted[day] = slots.flatMap((value) =>
        Array.from({ length: factor }, () => value),
      );
    } else if (fromSlots > toSlots && toSlots > 0) {
      const factor = Math.round(fromSlots / toSlots);
      converted[day] = Array.from({ length: toSlots }, (_, index) =>
        slots[index * factor],
      );
    } else {
      converted[day] = slots.slice();
    }
  }
  return converted;
}

function dayLabelsFor(locale) {
  // 2024-01-07 is a Sunday; index the labels by the schedule day key.
  const labels = [];
  for (let index = 0; index < 7; index += 1) {
    const date = new Date(Date.UTC(2024, 0, 7 + index));
    try {
      labels.push(
        new Intl.DateTimeFormat(locale, {
          weekday: "short",
          timeZone: "UTC",
        }).format(date),
      );
    } catch {
      labels.push(WEEKDAY_NAMES[index]);
    }
  }
  return labels;
}

function hourLabelFor(slot, slotCount, locale) {
  const minutes = Math.round(slot * slotMinutesFor(slotCount));
  const hour = Math.floor(minutes / 60) % 24;
  const minute = minutes % 60;
  try {
    return new Intl.DateTimeFormat(locale, {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZone: "UTC",
    }).format(new Date(Date.UTC(2024, 0, 1, hour, minute)));
  } catch {
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }
}

function minutesLabel(minutes) {
  const hour = Math.floor(minutes / 60);
  const minute = Math.round(minutes % 60);
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function slotRangeLabel(slot, slotCount) {
  const slotMinutes = slotMinutesFor(slotCount);
  return `${minutesLabel(slot * slotMinutes)}–${minutesLabel(
    (slot + 1) * slotMinutes,
  )}`;
}

function profileColor(profile, profiles) {
  const key = profiles[profile];
  return (key && PROFILE_COLORS[key]) || UNKNOWN_COLOR;
}

function noticeFor(state, schedule, shape) {
  if (state?.state === "unavailable") {
    return "unavailable";
  }
  if (!schedule) {
    return "noSchedule";
  }
  if (!shape.editable) {
    return "unsupported";
  }
  return null;
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

class SmartboxProgramCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = CARD_CSS;
    this.shadowRoot.appendChild(style);
    this._viewport = document.createElement("div");
    this._viewport.className = "viewport";
    this._viewport.addEventListener("click", (event) =>
      this._onCardClick(event),
    );
    this._viewport.addEventListener("keydown", (event) =>
      this._onCardKeydown(event),
    );
    this.shadowRoot.appendChild(this._viewport);

    this._config = null;
    this._hass = null;
    this._resolved = null;
    this._lastSig = "";
    this._strings = stringsFor("en");
    this._locale = "en";
    this._dayLabels = dayLabelsFor("en");
    this._firstWeekday = 1;
    this._saveState = "idle";
    this._saveTimer = null;
    this._dialog = null;
    this._dialogError = null;
    this._pendingClose = null;
    this._discardConfirm = null;
    this._hintId = `smartbox-editor-hint-${(nextEditorHintId += 1)}`;
  }

  static getStubConfig(hass) {
    const entries = scheduleEntries(hass);
    return entries.length ? { entity: entries[0].entity_id } : {};
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TYPE);
  }

  setConfig(config) {
    if (!config || typeof config !== "object" || Array.isArray(config)) {
      throw new Error("Invalid configuration");
    }
    const previous = this._config;
    this._config = { ...config };
    if (this._dialog && previous?.entity !== config.entity) {
      this._closeDialog();
    }
    this._lastSig = "";
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    const firstWeekday = firstWeekdayIndex(hass?.locale);
    if (firstWeekday !== this._firstWeekday) {
      this._firstWeekday = firstWeekday;
      this._lastSig = "";
    }
    const locale = hass?.locale?.language || hass?.language || "en";
    if (first || locale !== this._locale) {
      this._locale = locale;
      this._strings = stringsFor(locale);
      this._dayLabels = dayLabelsFor(locale);
      this._lastSig = "";
    }
    const resolved = this._resolve();
    this._resolved = resolved;
    if (
      this._saveState === "sent" &&
      resolved?.state?.state === "unavailable"
    ) {
      this._saveState = "unconfirmed";
    }
    this._syncDialog(resolved);
    const signature = this._signature(resolved);
    if (first || signature !== this._lastSig) {
      this._lastSig = signature;
      this._render();
    }
  }

  disconnectedCallback() {
    if (this._saveTimer) {
      clearTimeout(this._saveTimer);
      this._saveTimer = null;
    }
    this._saveState = "idle";
    this._closeDialog({ restoreFocus: false });
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _resolve() {
    const hass = this._hass;
    if (!hass) {
      return null;
    }
    const config = this._config || {};
    const registry = registryEntries(hass);
    const byEntity = new Map(registry.map((entry) => [entry.entity_id, entry]));
    const entityOnDevice = (deviceId, key) =>
      registry.find(
        (entry) =>
          entry.platform === "smartbox" &&
          entry.translation_key === key &&
          entry.device_id === deviceId,
      )?.entity_id || null;

    const entityId = config.entity || null;
    const entry = entityId ? byEntity.get(entityId) : null;
    let error = null;
    let deviceId = null;
    if (!entityId) {
      error = "chooseEntity";
    } else if (!entry) {
      error = "missingEntity";
    } else if (
      entry.platform !== "smartbox" ||
      entry.translation_key !== "schedule"
    ) {
      error = "notSmartbox";
    } else {
      deviceId = entry.device_id || null;
    }

    const state = entityId && !error ? hass.states?.[entityId] : undefined;
    if (entityId && !error && !state) {
      error = "missingEntity";
    }

    const rawSchedule = state?.attributes?.schedule;
    const schedule =
      rawSchedule &&
      typeof rawSchedule === "object" &&
      !Array.isArray(rawSchedule) &&
      Object.keys(rawSchedule).length
        ? rawSchedule
        : null;
    const profiles = profilesFor(state);
    const shape = scheduleShape(schedule, profiles.length);
    const notice = error ? null : noticeFor(state, schedule, shape);
    const temps = {};
    if (deviceId) {
      for (const [profile, key] of Object.entries(TEMP_KEYS)) {
        const tempId = entityOnDevice(deviceId, key);
        const tempState = tempId ? hass.states?.[tempId] : undefined;
        if (
          tempState &&
          tempState.state !== "unknown" &&
          tempState.state !== "unavailable"
        ) {
          temps[profile] = {
            id: tempId,
            value: tempState.state,
            unit: tempState.attributes?.unit_of_measurement || "",
          };
        }
      }
    }
    const device = deviceId ? hass.devices?.[deviceId] : null;
    const title =
      config.title ||
      device?.name_by_user ||
      device?.name ||
      state?.attributes?.friendly_name ||
      this._strings.program;
    return {
      entityId,
      deviceId,
      state,
      schedule,
      profiles,
      shape,
      resolution:
        state?.attributes?.resolution === 1 ||
        state?.attributes?.resolution === 0
          ? state.attributes.resolution
          : scheduleShape(schedule, profiles.length).slotCount === 48
            ? 1
            : 0,
      resolutionAvailable: state?.attributes?.resolution_available === true,
      notice,
      error,
      temps,
      title,
      locale: this._locale,
      firstWeekday: this._firstWeekday,
    };
  }

  _signature(resolved) {
    if (!this._hass) {
      return "no-hass";
    }
    const state = resolved?.state;
    return JSON.stringify([
      this._config?.entity || null,
      this._config?.title || null,
      legendEnabled(this._config),
      resolved?.entityId || null,
      resolved?.title || null,
      this._locale,
      resolved?.firstWeekday ?? null,
      resolved?.error || null,
      resolved?.notice || null,
      resolved?.shape?.editable || false,
      resolved?.shape?.slotCount || 0,
      resolved?.resolutionAvailable || false,
      resolved?.resolution ?? null,
      state?.attributes?.schedule
        ? JSON.stringify(state.attributes.schedule)
        : null,
      state?.attributes?.options
        ? state.attributes.options.join("|")
        : null,
      JSON.stringify(resolved?.temps || {}),
      this._saveState,
      this._hass.language || null,
      this._strings?.program || null,
    ]);
  }

  _onCardClick(event) {
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "edit") {
      const cell = event.target.closest("[data-day][data-slot]");
      this._openEditor(
        cell ? Number(cell.dataset.day) : undefined,
        cell ? Number(cell.dataset.slot) : undefined,
      );
      return;
    }
    if (action === "more-info") {
      const button = event.target.closest("[data-entity]");
      if (button?.dataset.entity) {
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            detail: { entityId: button.dataset.entity },
            bubbles: true,
            composed: true,
          }),
        );
      }
      return;
    }
    const cell = event.target.closest("[data-day][data-slot]");
    if (cell) {
      this._openEditor(
        Number(cell.dataset.day),
        Number(cell.dataset.slot),
      );
    }
  }

  _onCardKeydown(event) {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    if (!event.target.closest('[data-action="edit"]')) {
      return;
    }
    event.preventDefault();
    this._openEditor();
  }

  _render() {
    if (!this._viewport || !this._config) {
      return;
    }
    if (!this._hass) {
      this._viewport.innerHTML = "";
      return;
    }
    const resolved = this._resolved || this._resolve();
    const strings = this._strings;
    if (!resolved) {
      this._viewport.innerHTML = "";
      return;
    }
    if (resolved.error) {
      this._viewport.innerHTML = `<ha-card><div class="card-content" tabindex="-1"><div class="header"><div class="titles"><div class="title">${escapeHtml(
        resolved.title || strings.program,
      )}</div></div></div><div class="banner error">${escapeHtml(
        strings[resolved.error] || resolved.error,
      )}</div><div class="hint">${escapeHtml(
        strings.editHintUnsupported,
      )}</div></div></ha-card>`;
      return;
    }

    const title = escapeHtml(resolved.title || strings.program);
    const header = `<div class="header"><div class="titles"><div class="title">${title}</div></div></div>`;

    const canEdit = !resolved.notice && resolved.shape.editable;
    let preview = "";
    if (
      resolved.schedule &&
      resolved.shape.slotCount > 0 &&
      resolved.shape.slotCount <= MAX_SLOTS
    ) {
      const slotCount = resolved.shape.slotCount;
      const cellHeight = slotCount <= 24 ? 12 : 6;
      const axisStep = Math.max(1, Math.round(slotCount / 4));
      const displayDays = daysFromFirstWeekday(resolved.firstWeekday);
      const cells = ['<div class="preview-corner" aria-hidden="true"></div>'];
      for (const day of displayDays) {
        cells.push(
          `<div class="day">${escapeHtml(this._dayLabels[day])}</div>`,
        );
      }
      for (let slot = 0; slot < slotCount; slot += 1) {
        const label =
          slot % axisStep === 0
            ? String(Math.round((slot * 24) / slotCount)).padStart(2, "0")
            : "";
        cells.push(
          `<div class="preview-time" aria-hidden="true">${label}</div>`,
        );
        for (let position = 0; position < displayDays.length; position += 1) {
          const day = displayDays[position];
          const value = resolved.schedule[String(day)]?.[slot];
          const classes = ["cell"];
          if (position > 0) {
            classes.push("day-start");
          }
          if (
            slot === axisStep ||
            slot === axisStep * 2 ||
            slot === axisStep * 3
          ) {
            classes.push("hour-line");
          }
          if (slot === 0 && position === 0) {
            classes.push("corner-tl");
          } else if (slot === 0 && position === 6) {
            classes.push("corner-tr");
          } else if (slot === slotCount - 1 && position === 0) {
            classes.push("corner-bl");
          } else if (slot === slotCount - 1 && position === 6) {
            classes.push("corner-br");
          }
          cells.push(
            `<div class="${classes.join(" ")}" data-day="${day}" data-slot="${slot}" style="background:${profileColor(
              value,
              resolved.profiles,
            )}"></div>`,
          );
        }
      }
      const grid = `<div class="preview"${
        canEdit
          ? ' aria-hidden="true"'
          : ` role="img" aria-label="${escapeAttribute(strings.program)}"`
      } style="grid-template-columns:30px repeat(7, 1fr);--preview-cell-height:${cellHeight}px;--preview-corner-radius:${Math.min(
        8,
        cellHeight,
      )}px">${cells.join("")}</div>`;
      const overlay = canEdit
        ? `<span class="preview-edit" aria-hidden="true"><ha-icon icon="mdi:pencil"></ha-icon>${escapeHtml(
            strings.edit,
          )}</span>`
        : "";
      preview = canEdit
        ? `<div class="preview-button" role="button" tabindex="0" aria-label="${escapeAttribute(
            strings.edit,
          )}" data-action="edit">${grid}${overlay}</div>`
        : `<div class="preview-button">${grid}</div>`;
    }

    const showLegend = legendEnabled(this._config);
    let legend = "";
    if (showLegend && resolved.schedule) {
      legend = `<div class="legend">${resolved.profiles
        .map((key, index) => {
          const label = escapeHtml(strings[key] || key);
          const color = profileColor(index, resolved.profiles);
          const temp = resolved.temps[key];
          if (temp) {
            return `<button type="button" class="legend-chip" data-action="more-info" data-entity="${escapeAttribute(
              temp.id,
            )}"><span class="dot" style="background:${color}"></span>${label} <span class="temp">${escapeHtml(
              `${temp.value} ${temp.unit}`.trim(),
            )}</span></button>`;
          }
          return `<span class="legend-chip"><span class="dot" style="background:${color}"></span>${label}</span>`;
        })
        .join("")}</div>`;
    }

    const banners = [];
    if (resolved.notice) {
      banners.push(
        `<div class="banner">${escapeHtml(
          strings[resolved.notice] || resolved.notice,
        )}</div>`,
      );
    }
    if (this._saveState === "sent") {
      banners.push(
        `<div class="banner success">${escapeHtml(strings.sent)}</div>`,
      );
    }
    if (this._saveState === "unconfirmed") {
      banners.push(
        `<div class="banner error">${escapeHtml(strings.unconfirmed)}</div>`,
      );
    }

    const hint = resolved.notice
      ? resolved.notice === "noSchedule"
        ? strings.editHintNoSchedule
        : resolved.notice === "unavailable"
          ? strings.editHintUnavailable
          : strings.editHintUnsupported
      : !resolved.shape.editable
        ? strings.editHintUnsupported
        : "";

    this._viewport.innerHTML = `<ha-card><div class="card-content" tabindex="-1">${header}${preview}${legend}<div class="card-status" role="status" aria-live="polite"></div>${
      hint ? `<div class="hint">${escapeHtml(hint)}</div>` : ""
    }</div></ha-card>`;
    const status = this._viewport.querySelector(".card-status");
    if (status) {
      status.innerHTML = banners.join("");
    }
  }

  _openEditor(day, slot) {
    const resolved = this._resolved;
    if (
      !resolved ||
      resolved.error ||
      !resolved.schedule ||
      !resolved.shape.editable ||
      this._dialog
    ) {
      return;
    }
    this._original = cloneSchedule(resolved.schedule);
    this._draft = cloneSchedule(resolved.schedule);
    this._baselineJson = JSON.stringify(resolved.schedule);
    this._profiles = resolved.profiles.slice();
    this._slots = resolved.shape.slotCount;
    this._initialResolution =
      resolved.shape.slotCount === 48 ? 1 : 0;
    this._resolutionChanged = false;
    this._resolutionAvailable = resolved.resolutionAvailable;
    this._undo = [];
    this._brush = "cycle";
    this._brushIndex = null;
    this._dirty = false;
    this._sending = false;
    this._dialogError = null;
    this._externalChange = false;
    this._externalSchedule = null;
    this._painting = false;
    this._strokeUndoPushed = false;
    this._tapCandidate = null;
    this._selectedDay =
      Number.isInteger(day) && day >= 0 && day < 7
        ? day
        : resolved.firstWeekday;
    this._dayLabels = dayLabelsFor(resolved.locale);
    this._buildDialog(day, slot);
  }

  _buildDialog(day, slot) {
    const strings = this._strings;
    const resolved = this._resolved;
    const title = resolved.title || strings.program;
    const dialog = document.createElement("ha-dialog");
    dialog.innerHTML = `<div class="dialog-content">
        <div class="dialog-banners" role="status" aria-live="polite"></div>
        <div class="toolbar">
          <div class="brushes" role="group" aria-label="${escapeAttribute(
            strings.program,
          )}">
            <button type="button" class="brush" data-action="brush" data-profile="cycle" aria-pressed="true">${escapeHtml(
              strings.cycle,
            )}</button>
            ${this._profiles
              .map((key, index) => {
                const temp = resolved.temps[key];
                return `<button type="button" class="brush" data-action="brush" data-profile="${index}" aria-pressed="false"><span class="dot" style="background:${profileColor(
                  index,
                  this._profiles,
                )}"></span>${escapeHtml(
                  strings[key] || key,
                )}${
                  temp
                    ? `<span class="temp">${escapeHtml(
                        `${temp.value} ${temp.unit}`.trim(),
                      )}</span>`
                    : ""
                }</button>`;
              })
              .join("")}
          </div>
          <div class="tools">
            <button type="button" class="btn small" data-action="undo" disabled>${escapeHtml(
              strings.undo,
            )}</button>
            <button type="button" class="btn small" data-action="reset">${escapeHtml(
              strings.reset,
            )}</button>
            ${
              resolved.resolutionAvailable
                ? `<button type="button" class="btn small resolution-toggle" data-action="toggle-resolution" aria-pressed="${
                    this._slots === 48 ? "true" : "false"
                  }">${escapeHtml(strings.resolution)}: ${escapeHtml(
                    this._slots === 48
                      ? strings.halfHourly
                      : strings.hourly,
                  )}</button>`
                : ""
            }
          </div>
        </div>
        <div class="copy-row">
          <span class="copy-label" data-copy-label></span>
          <button type="button" class="btn small" data-action="copy-weekdays">${escapeHtml(
            strings.weekdays,
          )}</button>
          <button type="button" class="btn small" data-action="copy-weekend">${escapeHtml(
            strings.weekend,
          )}</button>
          <button type="button" class="btn small" data-action="copy-all">${escapeHtml(
            strings.allDays,
          )}</button>
        </div>
        <div class="hint" id="${this._hintId}">${escapeHtml(strings.instructions)}</div>
        <div class="grid-frame"><div class="grid" role="grid" aria-label="${escapeAttribute(
          strings.program,
        )}" aria-describedby="${this._hintId}"></div></div>
      </div>
      <div slot="footer" class="dialog-footer">
        <ha-button data-action="apply" variant="brand" appearance="filled" disabled>${escapeHtml(
          strings.apply,
        )}</ha-button>
      </div>`;
    this._dialog = {
      host: dialog,
      panel: dialog,
      grid: dialog.querySelector(".grid"),
      banners: dialog.querySelector(".dialog-banners"),
      apply: dialog.querySelector('[data-action="apply"]'),
      undo: dialog.querySelector('[data-action="undo"]'),
      reset: dialog.querySelector('[data-action="reset"]'),
      copyLabel: dialog.querySelector("[data-copy-label]"),
    };
    this._chromeSig = "";
    this._buildGrid();
    this._updateDialogChrome();

    dialog.addEventListener("click", (event) => this._onDialogClick(event));
    dialog.addEventListener(
      "click",
      (event) => {
        if (event.composedPath().some((el) => el?.dataset?.dialog === "close")) {
          event.preventDefault();
          event.stopPropagation();
          this._requestClose();
        }
      },
      true,
    );
    dialog.addEventListener(
      "keydown",
      (event) => this._onDialogKeydown(event),
      true,
    );
    dialog.addEventListener("pointerup", (event) =>
      this._onDialogPointerUp(event),
    );
    dialog.addEventListener("pointercancel", () =>
      this._clearTapCandidate(),
    );
    dialog.addEventListener("closed", () => this._onDialogClosed());
    const focusDay = Number.isInteger(day) ? day : this._selectedDay;
    const focusSlot = Number.isInteger(slot) ? slot : 0;
    dialog.addEventListener("opened", () =>
      requestAnimationFrame(() => this._focusCell(focusDay, focusSlot)),
    );
    this._dialog.grid.addEventListener("pointerdown", (event) =>
      this._onGridPointerDown(event),
    );
    this._dialog.grid.addEventListener("pointerover", (event) =>
      this._onGridPointerOver(event),
    );

    const openDialog = () => {
      if (!this._dialog || this._dialog.host !== dialog) {
        return;
      }
      dialog.hass = this._hass;
      dialog.width = "large";
      dialog.preventScrimClose = true;
      dialog.headerTitle = strings.edit;
      dialog.headerSubtitle = title;
      this.shadowRoot.appendChild(dialog);
      dialog.open = true;
    };
    if (customElements.get("ha-dialog")) {
      openDialog();
    } else {
      customElements.whenDefined("ha-dialog").then(openDialog);
    }
  }

  _buildGrid() {
    const grid = this._dialog.grid;
    grid.innerHTML = "";
    const slotCount = this._slots;
    const locale = this._resolved?.locale || this._locale;
    const displayDays = daysFromFirstWeekday(
      this._resolved?.firstWeekday ?? this._firstWeekday,
    );
    const header = document.createElement("div");
    header.className = "grid-row grid-head";
    header.setAttribute("role", "row");
    const headerCells = [
      `<div class="grid-corner" role="presentation"></div>`,
    ];
    for (const day of displayDays) {
      headerCells.push(
        `<button type="button" class="day-label" data-action="select-day" data-day="${day}" role="columnheader" aria-selected="false" aria-label="${escapeAttribute(
          this._strings.daySource.replace("{day}", this._dayLabels[day]),
        )}">${escapeHtml(this._dayLabels[day])}</button>`,
      );
    }
    header.innerHTML = headerCells.join("");
    grid.appendChild(header);
    const hourStep = slotCount / 4;
    for (let slot = 0; slot < slotCount; slot += 1) {
      const row = document.createElement("div");
      row.className = "grid-row";
      row.setAttribute("role", "row");
      const hourLine = slot > 0 && slot % hourStep === 0;
      const cells = [
        `<div class="slot-time" data-slot="${slot}" role="rowheader">${escapeHtml(
          hourLabelFor(slot, slotCount, locale),
        )}</div>`,
      ];
      for (let position = 0; position < displayDays.length; position += 1) {
        const day = displayDays[position];
        const value = this._draft[String(day)]?.[slot];
        const classes = ["cell"];
        if (position > 0) {
          classes.push("day-start");
        }
        if (hourLine) {
          classes.push("hour-line");
        }
        if (slot === 0 && position === 0) {
          classes.push("corner-tl");
        } else if (slot === 0 && position === 6) {
          classes.push("corner-tr");
        } else if (slot === slotCount - 1 && position === 0) {
          classes.push("corner-bl");
        } else if (slot === slotCount - 1 && position === 6) {
          classes.push("corner-br");
        }
        cells.push(
          `<button type="button" class="${classes.join(
            " ",
          )}" data-day="${day}" data-slot="${slot}" role="gridcell" tabindex="-1" aria-label="${escapeAttribute(
            this._cellLabel(day, slot, value),
          )}" style="background:${profileColor(
            value,
            this._profiles,
          )}"></button>`,
        );
      }
      row.innerHTML = cells.join("");
      grid.appendChild(row);
    }
    this._updateSelectedDay();
  }

  _cellLabel(day, slot, value) {
    const profile = this._profiles[value];
    const label = profile
      ? this._strings[profile] || profile
      : this._strings.unknown;
    return `${this._dayLabels[day]} ${slotRangeLabel(
      slot,
      this._slots,
    )}, ${label}`;
  }

  _updateCell(day, slot) {
    const cell = this._dialog?.grid.querySelector(
      `.cell[data-day="${day}"][data-slot="${slot}"]`,
    );
    if (!cell) {
      return;
    }
    const value = this._draft[String(day)]?.[slot];
    cell.style.background = profileColor(value, this._profiles);
    cell.setAttribute("aria-label", this._cellLabel(day, slot, value));
  }

  _refreshGrid() {
    for (let day = 0; day < 7; day += 1) {
      for (let slot = 0; slot < this._slots; slot += 1) {
        this._updateCell(day, slot);
      }
    }
    this._updateActionButtons();
  }

  _updateDirty() {
    this._dirty =
      JSON.stringify(this._draft) !== JSON.stringify(this._original);
    this._updateActionButtons();
  }

  _toggleResolution() {
    if (
      !this._dialog ||
      this._sending ||
      !this._resolutionAvailable ||
      ![24, 48].includes(this._slots)
    ) {
      return;
    }
    const target = this._slots === 24 ? 48 : 24;
    this._draft = convertScheduleResolution(this._draft, this._slots, target);
    this._slots = target;
    this._resolutionChanged =
      target !== (this._initialResolution === 1 ? 48 : 24);
    this._undo = [];
    this._buildGrid();
    this._updateDirty();
    this._updateDialogChrome();
  }

  _updateResolutionControl() {
    const button = this._dialog?.panel.querySelector(
      '[data-action="toggle-resolution"]',
    );
    if (!button) {
      return;
    }
    const halfHourly = this._slots === 48;
    button.textContent = `${this._strings.resolution}: ${
      halfHourly ? this._strings.halfHourly : this._strings.hourly
    }`;
    button.setAttribute("aria-pressed", halfHourly ? "true" : "false");
    button.disabled = this._sending;
  }

  _updateActionButtons() {
    if (!this._dialog) {
      return;
    }
    this._dialog.apply.disabled =
      this._sending || !this._dirty || this._externalChange;
    this._dialog.apply.textContent = this._sending
      ? this._strings.sending
      : this._strings.apply;
    this._dialog.undo.disabled = this._sending || this._undo.length === 0;
    this._dialog.reset.disabled = this._sending || !this._dirty;
    for (const button of this._dialog.panel.querySelectorAll(
      '[data-action="brush"], [data-action="select-day"], [data-action="copy-weekdays"], [data-action="copy-weekend"], [data-action="copy-all"]',
    )) {
      button.disabled = this._sending;
    }
    this._updateSelectedDay();
    this._updateBrushes();
    this._updateResolutionControl();
  }

  _updateBrushes() {
    if (!this._dialog) {
      return;
    }
    for (const button of this._dialog.panel.querySelectorAll(
      '[data-action="brush"]',
    )) {
      const selected =
        button.dataset.profile === "cycle"
          ? this._brush === "cycle"
          : Number(button.dataset.profile) === this._brushIndex;
      button.setAttribute("aria-pressed", selected ? "true" : "false");
      button.classList.toggle("selected", selected);
    }
  }

  _updateSelectedDay() {
    if (!this._dialog) {
      return;
    }
    for (const button of this._dialog.panel.querySelectorAll(
      '[data-action="select-day"]',
    )) {
      const selected = Number(button.dataset.day) === this._selectedDay;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-selected", selected ? "true" : "false");
    }
    if (this._dialog.copyLabel) {
      this._dialog.copyLabel.textContent = this._strings.copyTo.replace(
        "{day}",
        this._dayLabels[this._selectedDay],
      );
    }
  }

  _updateDialogChrome() {
    if (!this._dialog) {
      return;
    }
    const resolved = this._resolved;
    const state = resolved?.state;
    const signature = JSON.stringify([
      this._sending,
      this._dialogError,
      this._externalChange,
      this._undo.length,
      this._selectedDay,
      this._brush,
      this._brushIndex,
      state?.state === "unavailable",
      this._locale,
    ]);
    if (signature !== this._chromeSig) {
      this._chromeSig = signature;
      const banners = [];
      if (state?.state === "unavailable") {
        banners.push(
          `<div class="banner">${escapeHtml(this._strings.unavailable)}</div>`,
        );
      }
      if (this._externalChange) {
        const externalShape = scheduleShape(
          this._externalSchedule,
          this._profiles.length,
        );
        const canKeep =
          externalShape.editable &&
          externalShape.slotCount === this._slots;
        banners.push(
          `<div class="banner">${escapeHtml(
            this._strings.changedElsewhere,
          )}<span class="banner-actions"><button type="button" class="btn small" data-action="reload">${escapeHtml(
            this._strings.reload,
          )}</button>${
            canKeep
              ? `<button type="button" class="btn small" data-action="keep-mine">${escapeHtml(
                  this._strings.keepMine,
                )}</button>`
              : ""
          }</span></div>`,
        );
      }
      if (this._sending) {
        banners.push(
          `<div class="banner info">${escapeHtml(
            this._strings.sending,
          )}</div>`,
        );
      }
      if (this._dialogError) {
        banners.push(
          `<div class="banner error">${escapeHtml(
            this._dialogError,
          )}</div>`,
        );
      }
      this._dialog.banners.innerHTML = banners.join("");
    }
    this._updateActionButtons();
  }

  _syncDialog(resolved) {
    if (!this._dialog || !resolved) {
      return;
    }
    const state = resolved.state;
    const external = state?.attributes?.schedule;
    const current = JSON.stringify(external ?? null);
    if (
      external &&
      this._baselineJson !== null &&
      current !== this._baselineJson
    ) {
      const shape = scheduleShape(external, resolved.profiles.length);
      const compatible =
        shape.editable &&
        shape.slotCount === this._slots &&
        resolved.profiles.length === this._profiles.length;
      if (!compatible) {
        if (!this._dirty) {
          this._closeDialog();
          this._lastSig = "";
          this._render();
          return;
        }
        this._externalSchedule = external;
        this._externalChange = true;
      } else if (!this._dirty) {
        this._adoptExternal(external);
      } else {
        this._externalSchedule = external;
        this._externalChange = true;
      }
    }
    this._updateDialogChrome();
  }

  _adoptExternal(schedule) {
    const shape = scheduleShape(schedule, this._profiles.length);
    if (!shape.editable || shape.slotCount !== this._slots) {
      this._closeDialog();
      this._lastSig = "";
      this._render();
      return;
    }
    this._original = cloneSchedule(schedule);
    this._draft = cloneSchedule(schedule);
    this._baselineJson = JSON.stringify(schedule);
    this._initialResolution = shape.slotCount === 48 ? 1 : 0;
    this._resolutionChanged = false;
    this._undo = [];
    this._externalChange = false;
    this._externalSchedule = null;
    this._dirty = false;
    this._refreshGrid();
    this._updateDialogChrome();
  }

  _onDialogClick(event) {
    const button = event.target.closest("[data-action]");
    if (!button) {
      const cell = event.target.closest(".cell[data-day]");
      if (cell && event.detail === 0) {
        this._activateCell(
          Number(cell.dataset.day),
          Number(cell.dataset.slot),
        );
      }
      return;
    }
    const action = button.dataset.action;
    switch (action) {
      case "close":
      case "cancel":
        this._requestClose();
        break;
      case "apply":
        this._apply();
        break;
      case "undo":
        this._undoLast();
        break;
      case "reset":
        this._resetDraft();
        break;
      case "toggle-resolution":
        this._toggleResolution();
        break;
      case "brush":
        this._selectBrush(button.dataset.profile);
        break;
      case "select-day":
        this._selectedDay = Number(button.dataset.day);
        this._updateActionButtons();
        break;
      case "copy-weekdays":
        this._copySelectedTo([1, 2, 3, 4, 5]);
        break;
      case "copy-weekend":
        this._copySelectedTo([6, 0]);
        break;
      case "copy-all":
        this._copySelectedTo([0, 1, 2, 3, 4, 5, 6]);
        break;
      case "reload":
        if (this._externalSchedule) {
          this._adoptExternal(this._externalSchedule);
        }
        break;
      case "keep-mine": {
        const shape = scheduleShape(
          this._externalSchedule,
          this._profiles.length,
        );
        if (!shape.editable || shape.slotCount !== this._slots) {
          break;
        }
        this._baselineJson = JSON.stringify(
          this._externalSchedule ||
            this._resolved?.state?.attributes?.schedule ||
            null,
        );
        this._externalChange = false;
        this._externalSchedule = null;
        this._updateDialogChrome();
        break;
      }
      default:
        break;
    }
  }

  _onDialogKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      this._requestClose();
      return;
    }
    const cell = event.target.closest(".cell[data-day]");
    if (!cell) {
      return;
    }
    const day = Number(cell.dataset.day);
    const slot = Number(cell.dataset.slot);
    const displayDays = daysFromFirstWeekday(
      this._resolved?.firstWeekday ?? this._firstWeekday,
    );
    const position = displayDays.indexOf(day);
    let next = null;
    if (event.key === "ArrowLeft") {
      next = [displayDays[Math.max(0, position - 1)], slot];
    } else if (event.key === "ArrowRight") {
      next = [displayDays[Math.min(6, position + 1)], slot];
    } else if (event.key === "ArrowUp") {
      next = [day, Math.max(0, slot - 1)];
    } else if (event.key === "ArrowDown") {
      next = [day, Math.min(this._slots - 1, slot + 1)];
    } else if (event.key === "Home") {
      next = [displayDays[0], slot];
    } else if (event.key === "End") {
      next = [displayDays[6], slot];
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      this._applyBrush(day, slot, false);
      return;
    } else if (["1", "2", "3"].includes(event.key)) {
      const index = Number(event.key) - 1;
      if (index < this._profiles.length) {
        event.preventDefault();
        this._selectBrush(String(index));
      }
      return;
    }
    if (next) {
      event.preventDefault();
      this._focusCell(next[0], next[1]);
    }
  }

  _onGridPointerDown(event) {
    if (event.button !== 0 || this._sending) {
      return;
    }
    const cell = event.target.closest(".cell[data-day]");
    if (!cell) {
      return;
    }
    const day = Number(cell.dataset.day);
    const slot = Number(cell.dataset.slot);
    this._focusCell(day, slot);
    if (event.pointerType === "mouse") {
      event.preventDefault();
      this._beginPaint();
      this._applyBrush(day, slot, true);
      return;
    }
    this._tapCandidate = {
      day,
      slot,
      x: event.clientX,
      y: event.clientY,
      pointerId: event.pointerId,
    };
  }

  _onGridPointerOver(event) {
    if (!this._painting) {
      return;
    }
    const cell = event.target.closest(".cell[data-day]");
    if (!cell) {
      return;
    }
    const day = Number(cell.dataset.day);
    const slot = Number(cell.dataset.slot);
    const key = `${day}:${slot}`;
    if (key === this._lastPaintKey) {
      return;
    }
    this._lastPaintKey = key;
    this._applyBrush(day, slot, true);
  }

  _onDialogPointerUp(event) {
    if (!this._tapCandidate) {
      return;
    }
    const candidate = this._tapCandidate;
    this._clearTapCandidate();
    if (
      Math.abs(event.clientX - candidate.x) > 8 ||
      Math.abs(event.clientY - candidate.y) > 8
    ) {
      return;
    }
    const cell = event.target.closest(".cell[data-day]");
    if (
      !cell ||
      Number(cell.dataset.day) !== candidate.day ||
      Number(cell.dataset.slot) !== candidate.slot
    ) {
      return;
    }
    this._applyBrush(candidate.day, candidate.slot, false);
  }

  _clearTapCandidate() {
    this._tapCandidate = null;
  }

  _beginPaint() {
    if (this._painting) {
      return;
    }
    this._painting = true;
    this._strokeUndoPushed = false;
    this._lastPaintKey = null;
    this._onWindowPointerUp = () => this._endPaint();
    window.addEventListener("pointerup", this._onWindowPointerUp, true);
    window.addEventListener(
      "pointercancel",
      this._onWindowPointerUp,
      true,
    );
  }

  _endPaint() {
    this._painting = false;
    this._strokeUndoPushed = false;
    this._lastPaintKey = null;
    if (this._onWindowPointerUp) {
      window.removeEventListener(
        "pointerup",
        this._onWindowPointerUp,
        true,
      );
      window.removeEventListener(
        "pointercancel",
        this._onWindowPointerUp,
        true,
      );
      this._onWindowPointerUp = null;
    }
  }

  _activateCell(day, slot) {
    this._applyBrush(day, slot, false);
  }

  _applyBrush(day, slot, fromStroke) {
    if (this._sending) {
      return;
    }
    const row = this._draft?.[String(day)];
    if (!row || slot < 0 || slot >= row.length) {
      return;
    }
    const current = row[slot];
    const next =
      this._brush === "cycle"
        ? Number.isInteger(current)
          ? (current + 1) % this._profiles.length
          : 0
        : this._brushIndex;
    if (next === current) {
      return;
    }
    if (fromStroke) {
      if (!this._strokeUndoPushed) {
        this._pushUndo();
        this._strokeUndoPushed = true;
      }
    } else {
      this._pushUndo();
    }
    row[slot] = next;
    this._updateCell(day, slot);
    this._dirty =
      JSON.stringify(this._draft) !== JSON.stringify(this._original);
    this._updateActionButtons();
  }

  _pushUndo() {
    this._undo.push(cloneSchedule(this._draft));
    if (this._undo.length > 50) {
      this._undo.shift();
    }
    this._updateActionButtons();
  }

  _undoLast() {
    if (this._sending || !this._undo.length) {
      return;
    }
    this._draft = this._undo.pop();
    this._refreshGrid();
    this._updateDirty();
  }

  _resetDraft() {
    if (
      this._sending ||
      JSON.stringify(this._draft) === JSON.stringify(this._original)
    ) {
      return;
    }
    this._pushUndo();
    this._slots = this._initialResolution === 1 ? 48 : 24;
    this._resolutionChanged = false;
    this._draft = cloneSchedule(this._original);
    this._buildGrid();
    this._updateDirty();
  }

  _selectBrush(profile) {
    if (this._sending) {
      return;
    }
    if (profile === "cycle") {
      this._brush = "cycle";
      this._brushIndex = null;
    } else {
      const index = Number(profile);
      if (
        !Number.isInteger(index) ||
        index < 0 ||
        index >= this._profiles.length
      ) {
        return;
      }
      this._brush = profile;
      this._brushIndex = index;
    }
    this._updateBrushes();
  }

  _copySelectedTo(targets) {
    if (this._sending || !Number.isInteger(this._selectedDay)) {
      return;
    }
    const source = this._draft[String(this._selectedDay)];
    if (!source) {
      return;
    }
    const changedDays = [];
    for (const day of targets) {
      if (day === this._selectedDay) {
        continue;
      }
      if (
        JSON.stringify(this._draft[String(day)]) !== JSON.stringify(source)
      ) {
        changedDays.push(day);
      }
    }
    if (!changedDays.length) {
      return;
    }
    this._pushUndo();
    for (const day of changedDays) {
      this._draft[String(day)] = source.slice();
    }
    this._refreshGrid();
    this._updateDirty();
  }

  _focusCell(day, slot) {
    if (!this._dialog) {
      return;
    }
    const cells = this._dialog.grid.querySelectorAll(".cell[data-day]");
    for (const cell of cells) {
      cell.tabIndex = -1;
    }
    const cell = this._dialog.grid.querySelector(
      `.cell[data-day="${day}"][data-slot="${slot}"]`,
    );
    if (cell) {
      cell.tabIndex = 0;
      cell.focus();
    }
  }

  _requestClose() {
    if (!this._dialog || this._sending || this._discardConfirm) {
      return;
    }
    if (
      this._dirty &&
      JSON.stringify(this._draft) !== JSON.stringify(this._original)
    ) {
      this._openDiscardConfirm();
      return;
    }
    this._closeDialog();
  }

  _openDiscardConfirm() {
    if (this._discardConfirm || !this._dialog) {
      return;
    }
    const strings = this._strings;
    const host = document.createElement("ha-dialog");
    host.innerHTML = `<p class="confirm-text">${escapeHtml(
      strings.discardText,
    )}</p>
      <div slot="footer" class="dialog-footer">
        <ha-button data-action="keep-editing" variant="neutral" appearance="plain" autofocus>${escapeHtml(
          strings.cancel,
        )}</ha-button>
        <ha-button data-action="discard" variant="danger" appearance="filled">${escapeHtml(
          strings.discard,
        )}</ha-button>
      </div>`;
    host.hass = this._hass;
    host.type = "alert";
    host.width = "small";
    host.preventScrimClose = true;
    host.headerTitle = strings.discardPrompt;
    const confirm = { host, discard: false };
    this._discardConfirm = confirm;
    host.addEventListener("click", (event) => {
      const button = event.target.closest("[data-action]");
      if (!button || this._discardConfirm !== confirm) {
        return;
      }
      if (button.dataset.action === "discard") {
        confirm.discard = true;
        host.open = false;
      } else if (button.dataset.action === "keep-editing") {
        host.open = false;
      }
    });
    host.addEventListener("closed", () => {
      if (this._discardConfirm !== confirm) {
        host.remove();
        return;
      }
      this._discardConfirm = null;
      host.remove();
      if (confirm.discard) {
        this._closeDialog();
        return;
      }
      this._focusCell(this._selectedDay, 0);
    });
    this.shadowRoot.appendChild(host);
    const open = () => {
      if (this._discardConfirm === confirm) {
        host.open = true;
      }
    };
    if (customElements.get("ha-dialog")) {
      open();
    } else {
      customElements.whenDefined("ha-dialog").then(open);
    }
  }

  _closeDialog({ restoreFocus = true } = {}) {
    this._endPaint();
    if (!this._dialog) {
      return;
    }
    const dialog = this._dialog.host;
    if (!dialog || !dialog.open || !this.isConnected) {
      this._finishClose(restoreFocus);
      return;
    }
    this._pendingClose = { restoreFocus };
    dialog.open = false;
  }

  _onDialogClosed() {
    const pending = this._pendingClose;
    this._pendingClose = null;
    if (!pending && this._dirty && !this._sending && this._dialog?.host) {
      // A native dismissal slipped through; reopen and ask about the draft.
      const host = this._dialog.host;
      host.open = false;
      requestAnimationFrame(() => {
        if (!this._dialog || this._dialog.host !== host) {
          return;
        }
        host.open = true;
        this._openDiscardConfirm();
      });
      return;
    }
    this._finishClose(pending ? pending.restoreFocus : true);
  }

  _finishClose(restoreFocus = true) {
    const dialog = this._dialog?.host;
    if (!dialog) {
      return;
    }
    this._endPaint();
    const confirm = this._discardConfirm;
    this._discardConfirm = null;
    if (confirm) {
      confirm.host.open = false;
      confirm.host.remove();
    }
    this._dialog = null;
    this._pendingClose = null;
    dialog.remove();
    this._externalChange = false;
    this._externalSchedule = null;
    this._dialogError = null;
    this._lastSig = "";
    this._render();
    if (restoreFocus) {
      const edit = this._viewport.querySelector(
        '.preview-button[role="button"]',
      );
      const target = edit || this._viewport.querySelector(".card-content");
      target?.focus?.();
    }
  }

  async _apply() {
    if (
      !this._dialog ||
      this._sending ||
      this._externalChange ||
      this._discardConfirm
    ) {
      return;
    }
    const changed = diffDays(this._original, this._draft);
    if (!Object.keys(changed).length) {
      this._closeDialog();
      return;
    }
    const resolved = this._resolved;
    if (!resolved?.entityId || !this._hass?.callService) {
      this._dialogError = this._strings.saveFailed;
      this._updateDialogChrome();
      return;
    }
    this._sending = true;
    this._dialogError = null;
    this._updateDialogChrome();
    try {
      if (this._resolutionChanged) {
        await this._hass.callService(
          "smartbox",
          "set_schedule_resolution",
          { resolution: this._slots === 48 ? 1 : 0 },
          { entity_id: resolved.entityId },
        );
      }
      await this._hass.callService(
        "smartbox",
        "set_schedule",
        { schedule: changed },
        { entity_id: resolved.entityId },
      );
      this._original = cloneSchedule(this._draft);
      this._baselineJson = JSON.stringify(this._draft);
      this._sending = false;
      this._dirty = false;
      this._saveState = "sent";
      this._scheduleSaveSettle();
      this._closeDialog();
    } catch (error) {
      this._sending = false;
      this._dialogError =
        error?.message || this._strings.saveFailed;
      this._updateDialogChrome();
    }
  }

  _scheduleSaveSettle() {
    if (this._saveTimer) {
      clearTimeout(this._saveTimer);
    }
    this._saveTimer = setTimeout(() => {
      this._saveTimer = null;
      const state = this._hass?.states?.[this._resolved?.entityId];
      this._saveState =
        state?.state === "unavailable" ? "unconfirmed" : "idle";
      this._lastSig = "";
      this._render();
    }, SAVE_SETTLE_MS);
  }
}

class SmartboxProgramCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = EDITOR_CSS;
    this.shadowRoot.appendChild(style);
    this._config = {};
    this._hass = null;
    this._sig = "";
    this._form = null;
  }

  setConfig(config) {
    this._config = config || {};
    if (this._form) {
      this._form.data = this._formData();
    } else {
      this._render();
    }
  }

  set hass(hass) {
    this._hass = hass;
    const entries = scheduleEntries(hass);
    const sig = JSON.stringify([
      entries.map((entry) => [
        entry.entity_id,
        hass.states?.[entry.entity_id]?.attributes?.friendly_name,
      ]),
      hass?.language || null,
    ]);
    if (this._form) {
      this._form.hass = hass;
      if (sig !== this._sig) {
        this._sig = sig;
        this._updateForm(entries);
      }
    } else if (sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  _formData() {
    return {
      entity: this._config?.entity || "",
      title: this._config?.title || "",
      show_legend: legendEnabled(this._config),
    };
  }

  _schema(entries) {
    return [
      {
        name: "entity",
        selector: entries.length
          ? {
              entity: {
                include_entities: entries.map((entry) => entry.entity_id),
              },
            }
          : { text: {} },
      },
      { name: "title", selector: { text: {} } },
      { name: "show_legend", selector: { boolean: {} } },
    ];
  }

  _labels() {
    const strings = stringsFor(this._hass?.language);
    return {
      entity: strings.configEntity,
      title: strings.configTitle,
      show_legend: strings.configLegend,
    };
  }

  _render() {
    if (!this._hass) {
      return;
    }
    this.shadowRoot.innerHTML = `<style>${EDITOR_CSS}</style><div class="form"></div>`;
    const entries = scheduleEntries(this._hass);
    const form = document.createElement("ha-form");
    form.hass = this._hass;
    form.data = this._formData();
    form.schema = this._schema(entries);
    form.computeLabel = (schema) => this._labels()[schema.name] || schema.name;
    form.addEventListener("value-changed", (event) => {
      const value = event.detail.value || {};
      const config = { ...this._config };
      if (value.entity) {
        config.entity = value.entity;
      } else {
        delete config.entity;
      }
      if (value.title) {
        config.title = value.title;
      } else {
        delete config.title;
      }
      if (value.show_legend === false) {
        config.show_legend = false;
      } else {
        delete config.show_legend;
      }
      this._config = config;
      this._emit(config);
    });
    this._form = form;
    this.shadowRoot.querySelector(".form").appendChild(form);
  }

  _updateForm(entries) {
    if (!this._form) {
      this._render();
      return;
    }
    this._form.hass = this._hass;
    this._form.schema = this._schema(entries);
    this._form.data = this._formData();
    this._form.computeLabel = (schema) =>
      this._labels()[schema.name] || schema.name;
  }

  _emit(patch) {
    const config = { ...this._config, ...patch };
    if (!config.title) {
      delete config.title;
    }
    const showLegend = legendEnabled(config);
    if (showLegend) {
      delete config.show_legend;
    } else {
      config.show_legend = false;
    }
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      }),
    );
  }
}

function scheduleEntries(hass) {
  if (!hass) {
    return [];
  }
  return registryEntries(hass)
    .filter(
      (entry) =>
        entry.platform === "smartbox" &&
        entry.translation_key === "schedule" &&
        hass.states?.[entry.entity_id],
    )
    .sort((left, right) => {
      const leftName =
        hass.states?.[left.entity_id]?.attributes?.friendly_name ||
        left.entity_id;
      const rightName =
        hass.states?.[right.entity_id]?.attributes?.friendly_name ||
        right.entity_id;
      return leftName.localeCompare(rightName);
    });
}

if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, SmartboxProgramCard);
}
if (!customElements.get(EDITOR_TYPE)) {
  customElements.define(EDITOR_TYPE, SmartboxProgramCardEditor);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TYPE)) {
  window.customCards.push({
    type: CARD_TYPE,
    name: "Smartbox program",
    description: "Show and edit a Smartbox heater's weekly program.",
    preview: true,
  });
}
