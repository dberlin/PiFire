// Probes Configuration Javascript

// Global Variables
var deviceNameSelected = '';
var deviceModuleSelected = '';
var probeNameSelected = '';

// Bootstrap 4 always strips `modal-open` off <body> when ANY modal closes,
// even one nested inside another still-open modal (e.g. the Discover/Scan
// modals below, which live inside the Add/Edit Probe Device modal). Losing
// that class breaks the outer modal's `overflow-y: auto` and un-hides body
// scroll, so the page behind the modal scrolls instead of the modal itself.
// Put `modal-open` back if another modal is still shown.
$(document).on('hidden.bs.modal', '.modal', function () {
	if ($('.modal.show').length) {
		$('body').addClass('modal-open');
	}
});

// Bootstrap 4 binds each modal's [data-dismiss="modal"] click handling on
// that modal's OWN root element (delegated, bubble phase). Since our
// Discover/Scan modals are literal DOM descendants of the Add/Edit Probe
// Device modal (not siblings appended to <body>), clicking a nested modal's
// X/Close button bubbles past its own handler and keeps going up to the
// outer modal's identical handler, closing BOTH. Intercept in the capture
// phase -- before either bubble handler runs -- and, for a nested modal
// only, close just that modal ourselves and stop the event there. Modals
// with no ancestor modal (the normal case) are untouched.
document.addEventListener(
	'click',
	function (event) {
		const dismissBtn = event.target.closest('[data-dismiss="modal"]');
		if (!dismissBtn) return;
		const modal = dismissBtn.closest('.modal');
		if (!modal || !modal.parentElement || !modal.parentElement.closest('.modal')) return;
		event.stopPropagation();
		$(modal).modal('hide');
	},
	true,
);

//
// Bluetooth Scanning Functions
//
function scanBluetooth(itemID) {
	const modal = '#bt_' + itemID + '_Modal';
	const modalContent = '#bt_' + itemID + '_Select';
	$(modal).modal('show');
	// Show scanning text while scanning
	$(modalContent).html('<br> \
                <h4>Scanning...</h4> \
                <br> \
                <div class="fa-3x"> \
                    <i class="fa-solid fa-magnifying-glass fa-bounce"></i> \
                </div> \
                <br></br>');
	// Load the bluetooth scan page
	$(modalContent).load("/wizard/bt_scan", {"itemID" : itemID});
}

function selectBluetoothDevice(hw_id, itemID) {
	const modal = '#bt_' + itemID + '_Modal';
	const item = '#' + itemID;
	$(item).val(hw_id);
	// Hide the modal
	$(modal).modal('hide');
}

//
// ThermoWorks Cloud Discovery Functions
//
function scanThermoworksDevices(emailID, passwordID, serialID, numProbesID) {
	const modal = '#tw_' + serialID + '_Modal';
	const modalContent = '#tw_' + serialID + '_Select';
	const email = $('#' + emailID).val();
	const password = $('#' + passwordID).val();
	$(modal).modal('show');
	// Show connecting text while discovering
	$(modalContent).html('<br> \
                <h4>Connecting...</h4> \
                <br> \
                <div class="fa-3x"> \
                    <i class="fa-solid fa-magnifying-glass fa-bounce"></i> \
                </div> \
                <br></br>');
	// Load the discovery results
	$(modalContent).load("/wizard/thermoworks_discover", {
		"email": email,
		"password": password,
		"serialID": serialID,
		"numProbesID": numProbesID,
	});
}

function selectThermoworksDevice(serial, numChannels, serialID, numProbesID) {
	const modal = '#tw_' + serialID + '_Modal';
	$('#' + serialID).val(serial);
	$('#' + numProbesID).val(numChannels);
	// Hide the modal
	$(modal).modal('hide');
}

//
// I2C Bus Discovery Functions
//
function scanI2CBus(itemID, kindItemID) {
	const modal = '#i2c_' + itemID + '_Modal';
	const modalContent = '#i2c_' + itemID + '_Select';
	const kind = $('#' + kindItemID).val();
	$(modal).modal('show');
	// Show scanning text while scanning
	$(modalContent).html('<br> \
                <h4>Scanning...</h4> \
                <br> \
                <div class="fa-3x"> \
                    <i class="fa-solid fa-magnifying-glass fa-bounce"></i> \
                </div> \
                <br></br>');
	// Load the I2C bus scan results
	$(modalContent).load("/wizard/i2c_bus_scan", {"itemID" : itemID, "kind" : kind});
}

function selectI2CBus(value, itemID) {
	const modal = '#i2c_' + itemID + '_Modal';
	$('#' + itemID).val(value);
	// Hide the modal
	$(modal).modal('hide');
}

//
// USB Serial Device Discovery Functions
//
function scanUsbSerial(itemID, vid, pid) {
	const modal = '#i2c_' + itemID + '_Modal';
	const modalContent = '#i2c_' + itemID + '_Select';
	$(modal).modal('show');
	// Show scanning text while scanning
	$(modalContent).html('<br> \
                <h4>Scanning...</h4> \
                <br> \
                <div class="fa-3x"> \
                    <i class="fa-solid fa-magnifying-glass fa-bounce"></i> \
                </div> \
                <br></br>');
	// Load the USB serial scan results
	$(modalContent).load("/wizard/usb_serial_scan", {"itemID" : itemID, "vid" : vid, "pid" : pid});
}

//
// Device Functions
//

// Select a Device
function probe_selectDevice(deviceName) {
	deviceNameSelected = deviceName;
};

// Delete the Selected Device
function probe_deleteDevice(modalName) {
	// Delay the delete command for 500ms, so that the modal can close down
	var send_delayed_cmd = setInterval(function(){
		$('#probeDevicesCard').load("/probeconfig", {"action" : "delete_device", "section" : "devices", "name" : deviceNameSelected});
		clearInterval(send_delayed_cmd);
		$('#probePortsCard').load("/probeconfig", {"action" : "refresh_probes", "section" : "ports"});
	}, 500);
};

// Load Device Module Configuration / Settings into modal 
function probe_addShowDeviceConfig(module) {
	deviceModuleSelected = module;
	//load module information into modal
	$('#addProbeDeviceField').load("/probeconfig", {"action" : "add_config", "section" : "devices", "module" : deviceModuleSelected});
};

function probe_addSubmitDeviceConfig() {
	var send_delayed_cmd = setInterval(function(){
		//get device_module type from the select box
		var device_name = $("#probeDeviceNameAdd").val();

		//get all configuration data 
		var response = {};

		const collection = document.getElementsByClassName("deviceSpecificAdd");
		for (let i = 0; i < collection.length; i++) {
			var setting_id = collection[i].id;
			var setting_val = $('#'+setting_id).val();
			response[setting_id] = setting_val;
		}
		response['action'] = 'add_device';
		response['section'] = 'devices';
		response['name'] = device_name;
		response['module'] = deviceModuleSelected;

		$('#probeDevicesCard').load("/probeconfig", response);
		clearInterval(send_delayed_cmd);
		$('#addProbeDeviceField').html('');
	}, 500);
};

function probe_editShowDeviceConfig(deviceName) {
	deviceNameSelected = deviceName;
	//load module information into modal
	$('#editProbeDeviceField').load("/probeconfig", {"action" : "edit_config", "section" : "devices", "name" : deviceName});
};

function probe_editSubmitDeviceConfig() {
	var send_delayed_cmd = setInterval(function(){
		var new_device_name = $("#probeDeviceNameEdit").val();

		//get all configuration data 
		var response = {};

		const collection = document.getElementsByClassName("deviceSpecificEdit");
		for (let i = 0; i < collection.length; i++) {
			var setting_id = collection[i].id;
			var setting_val = $('#'+setting_id).val();
			response[setting_id] = setting_val;
		}
		response['action'] = 'edit_device';
		response['section'] = 'devices';
		response['name'] = deviceNameSelected;
		response['newname'] = new_device_name;
		response['module'] = deviceModuleSelected;

		$('#probeDevicesCard').load("/probeconfig", response);
		clearInterval(send_delayed_cmd);
		// After getting values, clear data so that it doesn't interfere with other data
		$('#editProbeDeviceField').html('');
	}, 500);
};

// 
// Probe / Port Functions 
// 

// Select a Device
function probe_selectProbe(probeName) {
	probeNameSelected = probeName;
	console.log('Selecting: ' + probeNameSelected);
};

// Delete the Selected Device
function probe_deleteProbe() {
	console.log('Deleting Port/Probe: ' + probeNameSelected);
	// Delay the delete command for 500ms, so that the modal can close down
	var send_delayed_cmd = setInterval(function(){
		$('#probePortsCard').load("/probeconfig", {"action" : "delete_probe", "section" : "ports", "label" : probeNameSelected});
		clearInterval(send_delayed_cmd);
	}, 500);
};

// Load Device Module Configuration / Settings into modal 
function probe_showProbeConfig(probeName) {
	console.log('Showing Port/Probe Configuration');
	probeNameSelected = probeName;
	//load module information into modal
	if (probeName == '') {
		fieldName = '#addProbePortField';		
	} else {
		fieldName = '#editProbePortField';		
	};
	$(fieldName).load("/probeconfig", {"action" : "config", "section" : "ports", "label" : probeName});
};

// Submit probe config (add/edit)
function probe_submitProbeConfig(request) {
	if (request == 'add') {
		console.log('Adding Port/Probe.');
		// Clear the EDIT probe modal so that it doesn't interfere with getting data. 
		$('#editProbePortField').html('');
	} else {
		console.log('Editing Port/Probe.');
		// Clear the ADD probe modal so that it doesn't interfere with getting data. 
		$('#addProbePortField').html('');
	};

	var send_delayed_cmd = setInterval(function(){
		//get all configuration data 
		var response = {};

		const collection = document.getElementsByClassName("probeConfig");
		for (let i = 0; i < collection.length; i++) {
			var setting_id = collection[i].id;
			var setting_val = $('#'+setting_id).val();
			response[setting_id] = setting_val;
		}

		if (request == 'add') {
			response['action'] = 'add_probe';
		} else {
			response['action'] = 'edit_probe';
		};
		
		response['section'] = 'ports';
		response['name'] = probeNameSelected;

		$('#probePortsCard').load("/probeconfig", response);

		// After getting values, clear data so that it doesn't interfere with other data
		$('#editProbePortField').html('');
		$('#addProbePortField').html('');

		clearInterval(send_delayed_cmd);
	}, 500);
};

function probe_showHideFields(val, id) {
	// Show/Hide the profile ID field depending if port is an ADC port
	if (id == 'probe_config_device_port') {
		if (val.includes("ADC")) {
			$("#probe_config_row_profile_id").show();
		} else {
			$("#probe_config_row_profile_id").hide();
		}
	} else if (id == 'probe_config_type') {
		if (val.includes("Aux")) {
			$("#probe_config_row_enabled").hide();
		} else {
			$("#probe_config_row_enabled").show();
		}
	}
}

