.pragma library

// Shared routing for menu items and control-panel buttons.
// handlers: { backend, openMenu(name), openInput(name, origin), close() } — any
// may be omitted. Mirrors the pygame _process_touch / _command_handler routing.
function activate(item, handlers) {
	var a = item.action;
	if (a === "cmd_none")
		return;
	if (a === "menu_close") {
		if (handlers.close)
			handlers.close();
		return;
	}
	if (a.indexOf("menu_") === 0) {
		if (handlers.openMenu)
			handlers.openMenu(a.substring(5));
		return;
	}
	if (a.indexOf("input_") === 0) {
		if (handlers.openInput)
			handlers.openInput(a.substring(6), item.origin !== undefined ? item.origin : "");
		return;
	}
	if (handlers.backend)
		handlers.backend.action(a, item.value !== undefined ? item.value : 0);
	if (handlers.close)
		handlers.close();
}
