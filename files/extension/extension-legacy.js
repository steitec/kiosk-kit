// GNOME 40-44: since GNOME 40 the shell starts in the Activities overview.
// During startup we report "no overview", afterwards the normal state returns.
const Main = imports.ui.main;

let startupId = 0;

function init() {}

function enable() {
  if (!Main.layoutManager._startingUp)
    return;
  Main.sessionMode.hasOverview = false;
  startupId = Main.layoutManager.connect('startup-complete', () => {
    Main.sessionMode.hasOverview = true;
    Main.layoutManager.disconnect(startupId);
    startupId = 0;
  });
}

function disable() {
  if (startupId) {
    Main.layoutManager.disconnect(startupId);
    startupId = 0;
  }
  Main.sessionMode.hasOverview = true;
}
