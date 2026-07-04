.pragma library

var MENUS = {
	"main": {title: "Menu", items: [
		{label: "Prime", action: "menu_prime"},
		{label: "Startup", action: "cmd_startup"},
		{label: "Monitor", action: "cmd_monitor"},
		{label: "System", action: "menu_system"},
		{label: "Close", action: "menu_close"}]},
	"main_active_normal": {title: "Menu", items: [
		{label: "Lid Open", action: "cmd_lid_open"},
		{label: "Hold", action: "input_hold"},
		{label: "Shutdown", action: "cmd_shutdown"},
		{label: "Stop", action: "cmd_stop"},
		{label: "Smoke", action: "cmd_smoke"},
		{label: "Smoke+", action: "cmd_splus"},
		{label: "PMode", action: "menu_pmode"},
		{label: "System", action: "menu_system"},
		{label: "Close", action: "menu_close"}]},
	"main_active_monitor": {title: "Menu", items: [
		{label: "Stop", action: "cmd_stop"},
		{label: "System", action: "menu_system"},
		{label: "Close", action: "menu_close"}]},
	"main_active_recipe": {title: "Menu", items: [
		{label: "Next Step", action: "cmd_next_step"},
		{label: "Shutdown", action: "cmd_shutdown"},
		{label: "Stop", action: "cmd_stop"},
		{label: "Smoke+", action: "cmd_splus"},
		{label: "System", action: "menu_system"},
		{label: "Close", action: "menu_close"}]},
	"system": {title: "System", items: [
		{label: "Show QR Code", action: "menu_qrcode"},
		{label: "Reboot System", action: "menu_main_reboot"},
		{label: "Power Off System", action: "menu_main_power_off"},
		{label: "Close", action: "menu_close"}]},
	"main_reboot": {title: "Reboot?", items: [
		{label: "Yes", action: "cmd_reboot"},
		{label: "No", action: "menu_close"}]},
	"main_power_off": {title: "Power Off?", items: [
		{label: "Yes", action: "cmd_poweroff"},
		{label: "No", action: "menu_close"}]},
	"prime": {title: "Prime then Startup?", items: [
		{label: "Yes", action: "menu_prime_startup"},
		{label: "No", action: "menu_prime_only"}]},
	"prime_startup": {title: "Prime + Startup", items: [
		{label: "10 grams", action: "cmd_primestartup", value: 10},
		{label: "25 grams", action: "cmd_primestartup", value: 25},
		{label: "50 grams", action: "cmd_primestartup", value: 50},
		{label: "Close", action: "menu_close"}]},
	"prime_only": {title: "Prime Only", items: [
		{label: "10 grams", action: "cmd_primeonly", value: 10},
		{label: "25 grams", action: "cmd_primeonly", value: 25},
		{label: "50 grams", action: "cmd_primeonly", value: 50},
		{label: "Close", action: "menu_close"}]},
	"startup": {title: "Startup?", items: [
		{label: "Yes", action: "cmd_startup"},
		{label: "No", action: "menu_close"}]},
	"pmode": {title: "P-Mode", items: [
		{label: "PMode 0", action: "cmd_pmode", value: 0},
		{label: "PMode 1", action: "cmd_pmode", value: 1},
		{label: "PMode 2", action: "cmd_pmode", value: 2},
		{label: "PMode 3", action: "cmd_pmode", value: 3},
		{label: "PMode 4", action: "cmd_pmode", value: 4},
		{label: "PMode 5", action: "cmd_pmode", value: 5},
		{label: "PMode 6", action: "cmd_pmode", value: 6},
		{label: "PMode 7", action: "cmd_pmode", value: 7},
		{label: "PMode 8", action: "cmd_pmode", value: 8},
		{label: "PMode 9", action: "cmd_pmode", value: 9},
		{label: "Close", action: "menu_close"}]},
	"message": {title: "Message", items: [
		{label: "OK", action: "menu_close"}]}
};

function menuFor(name) {
	return MENUS[name] || MENUS["main"];
}

function mainVariantForMode(mode) {
	if (mode === "Stop") return "main";
	if (mode === "Monitor") return "main_active_monitor";
	if (mode === "Recipe") return "main_active_recipe";
	return "main_active_normal";
}
