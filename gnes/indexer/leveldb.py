import pickle
from threading import Thread, Event
from typing import List, Any

import plyvel

from .base import BaseTextIndexer
from ..document import BaseDocument


class LVDBIndexer(BaseTextIndexer):
    def __init__(self, data_path: str, *args, **kwargs):
        super().__init__()
        self.data_path = data_path
        self._db = plyvel.DB(data_path, create_if_missing=True)
        self._NOT_FOUND = {}

    def __getstate__(self):
        d = super().__getstate__()
        del d['_db']
        return d

    def __setstate__(self, d):
        super().__setstate__(d)
        self._db = plyvel.DB(self.data_path, create_if_missing=True)

    def add(self, keys: List[int], docs: List[Any], *args, **kwargs):
        with self._db.write_batch() as wb:
            for k, d in zip(keys, docs):
                doc_id = pickle.dumps(k)
                doc = pickle.dumps(d)
                wb.put(doc_id, doc)

    def query(self, keys: List[int], top_k: int = 1, *args, **kwargs) -> List[Any]:
        res = []
        for k in keys:
            doc_id = pickle.dumps(k)
            v = self._db.get(doc_id)
            res.append(pickle.loads(v) if v else self._NOT_FOUND)
        return res

    def close(self):
        super().close()
        self._db.close()

    @staticmethod
    def _int2bytes(x: int) -> bytes:
        return x.to_bytes((x.bit_length() + 7) // 8, 'big')

    @staticmethod
    def _bytes2int(xbytes: bytes) -> int:
        return int.from_bytes(xbytes, 'big')


class AsyncLVDBIndexer(LVDBIndexer):
    def __init__(self, data_path: str, *args, **kwargs):
        super().__init__(data_path, *args, **kwargs)
        self._is_busy = Event()
        self._exit_signal = Event()
        self._jobs = []
        self._thread = Thread(target=self._db_write, args=(), kwargs=None)
        self._thread.setDaemon(1)
        self._thread.start()

    def add(self, keys: List[int], docs: List[BaseDocument], *args, **kwargs):
        self._jobs.append((keys, docs))

    def query(self, keys: List[int], top_k: int = 1, *args, **kwargs) -> List[Any]:
        self._is_busy.wait()
        return super().query(keys, top_k)

    def _add(self, keys: List[int], docs: List[BaseDocument], *args, **kwargs):
        self._is_busy.set()
        super().add(keys, docs)
        self._is_busy.clear()

    def _db_write(self):
        while not self._exit_signal:
            if self._jobs:
                keys, docs = self._jobs.pop()
                self._add(keys, docs)

    def __getstate__(self):
        d = super().__getstate__()
        del d['_thread']
        del d['_is_busy']
        del d['_exit_signal']
        return d

    def __setstate__(self, d):
        super().__setstate__(d)
        self._thread = Thread(target=self._db_write, args=(), kwargs=None)
        self._thread.setDaemon(1)
        self._thread.start()
        self._is_busy = Event()
        self._jobs = []

    def close(self):
        self._jobs.clear()
        self._exit_signal.set()
        self._thread.join()
        super().close()
