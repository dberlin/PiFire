"""Shared helper: drain the system-command queue, run each supported command on
the grill platform, and push results to the output queue.

Lives in its own low-level module (imports nothing from control.py, the
Controller, or the mode handlers) because it is called from BOTH sides of the
control process: every work cycle (controller.runtime.modes.base.ControlMode.run)
and the outer control loop (controller.runtime.controller.Controller.tick). A
neutral home avoids an import cycle between those.
"""


def process_system_commands(ctx):
	grill_platform = ctx.devices.grill_platform
	# Setup access to the system command queue
	system_commands = ctx.store.system_commands()
	# Setup access to the system output queue
	system_output = ctx.store.system_output()
	# Initialize variable for supported commands (only look for supported commands if we have something to process)
	supported_cmds = []

	while system_commands.length() > 0:
		if supported_cmds == []:
			# Get list of supported system commands
			supported_cmds = grill_platform.supported_commands(None)['data']['supported_cmds']
		command = system_commands.pop()
		if command[0] in supported_cmds:
			command_method = getattr(grill_platform, command[0])
			result = command_method(command)
			result['command'] = command
		else:
			result = {
				'command': command,
				'result': 'ERROR',
				'message': f'ERROR: Command [{command[0]}] is not supported with the current platform.',
				'data': {},
			}
		system_output.push(result)
