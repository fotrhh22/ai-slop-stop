from __future__ import annotations
import asyncio
import time


class TransmissionError(RuntimeError):
    def __init__(self, completed, attempted, cause):
        self.completed = completed
        self.attempted = attempted
        super().__init__(f'TRANSMISSION_UNKNOWN: completed_bytes={completed}, attempted_bytes={attempted}, cause={type(cause).__name__}; no automatic retry')


def chunks(payload, size):
    if not 1 <= size <= 512:
        raise ValueError('chunk size must be between 1 and 512 bytes')
    return [payload[i:i+size] for i in range(0, len(payload), size)]


async def write_ble(client, characteristic, payload, size, response, interval, timeout=10):
    properties = set(characteristic.properties)
    if ('write' if response else 'write-without-response') not in properties:
        raise ValueError('Selected characteristic does not support the requested write type')
    limit = 512 if response else characteristic.max_write_without_response_size
    size = min(size, limit)
    blocks = chunks(payload, size)
    completed = attempted = 0
    for index, block in enumerate(blocks):
        attempted = completed + len(block)
        try:
            if not client.is_connected:
                raise ConnectionError('disconnected')
            await asyncio.wait_for(client.write_gatt_char(characteristic, block, response=response), timeout)
        except Exception as exc:
            raise TransmissionError(completed, attempted, exc) from exc
        completed += len(block)
        if index + 1 < len(blocks):
            await asyncio.sleep(interval)
    return completed


def write_serial(port, payload, size, interval):
    completed = attempted = 0
    blocks = chunks(payload, size)
    for index, block in enumerate(blocks):
        cursor = 0
        try:
            while cursor < len(block):
                remaining = block[cursor:]
                attempted = completed + len(remaining)
                written = port.write(remaining)
                if not isinstance(written, int) or written <= 0 or written > len(remaining):
                    raise OSError('invalid or zero serial write')
                cursor += written
                completed += written
        except Exception as exc:
            raise TransmissionError(completed, attempted, exc) from exc
        if index + 1 < len(blocks):
            time.sleep(interval)
    return completed
