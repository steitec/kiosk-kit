// GNOME 45+: same as extension-legacy.js, in the ES module format GNOME 45 requires.
// Untested on hardware so far -- the reference panel runs GNOME 43.
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class KioskNoOverview extends Extension {
  enable() {
    this._startupId = 0;
    if (!Main.layoutManager._startingUp)
      return;
    Main.sessionMode.hasOverview = false;
    this._startupId = Main.layoutManager.connect('startup-complete', () => {
      Main.sessionMode.hasOverview = true;
      Main.layoutManager.disconnect(this._startupId);
      this._startupId = 0;
    });
  }

  disable() {
    if (this._startupId) {
      Main.layoutManager.disconnect(this._startupId);
      this._startupId = 0;
    }
    Main.sessionMode.hasOverview = true;
  }
}
