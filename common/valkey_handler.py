"""
Class to create a generic valkey handler for logging
"""

import logging


class ValkeyHandler(logging.Handler):
	def __init__(self, valkey_client, valkey_key_prefix):
		super().__init__()
		self.valkey_client = valkey_client
		self.valkey_key_prefix = valkey_key_prefix

	def emit(self, record):
		message = self.format(record)
		key = f'{self.valkey_key_prefix}'
		self.valkey_client.lpush(key, message)

	def flush(self):
		self.valkey_client.delete(self.valkey_key_prefix)
