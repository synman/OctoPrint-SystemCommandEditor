/*
 * System Command Editor: adds icons and section names to OctoPrint's System menu.
 *
 * The menu itself is core's. This only adds nodes to it, each marked with the class "sce-added", and removes its
 * own nodes before adding them again. Section names reach the menu with each divider. Icons do not, so they are
 * read from the settings, which only users allowed to change settings can load. Everyone else gets the plain menu.
 */
$(function () {
    const ADDED = "sce-added";
    const DOUBLED = "sce-doubled";

    function SystemCommandEditorMenuViewModel(parameters) {
        const self = this;

        self.settingsViewModel = parameters[0];
        self.systemViewModel = parameters[1];

        self._pending = undefined;

        self._icons = () => {
            const icons = {};
            _.each(ko.toJS(self.settingsViewModel.system_actions) || [], (entry) => {
                if (entry && entry.action && entry.icon) {
                    icons[entry.action] = entry.icon;
                }
            });
            return icons;
        };

        self.decorate = () => {
            self._pending = undefined;

            const menu = $("#navbar_systemmenu > ul.dropdown-menu");
            if (!menu.length) return;

            menu.find("." + ADDED).remove();
            menu.children("." + DOUBLED).removeClass(DOUBLED);
            menu.toggleClass("sce-menu", true);

            // every entry gets an icon, or an empty one in its place, so the names line up
            const icons = self._icons();
            // icons and headings for OctoPrint's own entries come from this plugin's settings
            const plugin = self.settingsViewModel.settings.plugins && self.settingsViewModel.settings.plugins.systemcommandeditor;
            const CORE = (plugin && ko.toJS(plugin.core)) || {};
            menu.children("li").each(function () {
                const li = $(this);
                const data = ko.dataFor(this);
                if (!data) return;

                if (data.actionSource !== "custom") {
                    if (data.action === "divider") return;
                    const core = data.actionSource === "core" ? CORE[data.action] : undefined;
                    if (core && core.heading) {
                        $("<li></li>")
                            .addClass("dropdown-header " + ADDED)
                            .text(core.heading)
                            .insertBefore(li);
                    }
                    $("<i></i>")
                        .addClass(((core && core.icon) || "sce-menu-icon-blank") + " fa-fw sce-menu-icon " + ADDED)
                        .prependTo(li.children("a").first());
                    return;
                }

                if (data.action === "divider") {
                    // core puts its own divider before the custom commands; don't draw a second line
                    if (li.prevAll("li").first().hasClass("divider")) {
                        li.addClass(DOUBLED);
                    }
                    if (data.name) {
                        $("<li></li>")
                            .addClass("dropdown-header " + ADDED)
                            .text(data.name)
                            .insertAfter(li);
                    }
                    return;
                }

                const icon = icons[data.action] || "sce-menu-icon-blank";
                if (icon) {
                    $("<i></i>")
                        .addClass(icon + " fa-fw sce-menu-icon " + ADDED)
                        .prependTo(li.children("a").first());
                }
            });
        };

        self.schedule = () => {
            // knockout re-renders the menu after the array changes; decorate once that is done
            if (self._pending) return;
            self._pending = window.setTimeout(self.decorate, 0);
        };

        self.onStartupComplete = () => {
            self.systemViewModel.systemActions.subscribe(self.schedule);
            self.settingsViewModel.system_actions.subscribe(self.schedule);
            self.schedule();
        };

        self.onEventSettingsUpdated = self.schedule;
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: SystemCommandEditorMenuViewModel,
        dependencies: ["settingsViewModel", "systemViewModel"]
    });
});
