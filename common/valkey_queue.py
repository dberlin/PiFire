"""
Class to create a generic valkey based queue
"""

import valkey
import json


class ValkeyQueue:
	def __init__(self, hashname):
		self.hashname = hashname
		self.valkey_db = valkey.StrictValkey('localhost', 6379, charset='utf-8', decode_responses=True)

	def push(self, data):
		self.valkey_db.rpush(self.hashname, json.dumps(data))

	def pop(self):
		popped = None
		if self.length() > 0:
			popped = json.loads(self.valkey_db.lpop(self.hashname))
		return popped

	def length(self):
		return self.valkey_db.llen(self.hashname)

	def list(self, start=0, end=-1):
		data = self.valkey_db.lrange(self.hashname, start, end)
		output = []
		while len(data) > 0:
			output.append(json.loads(data.pop(0)))
		return output

	def flush(self):
		self.valkey_db.delete(self.hashname)
