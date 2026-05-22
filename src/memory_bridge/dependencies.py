from .storage import MemoryStorage

storage = MemoryStorage()


async def get_storage() -> MemoryStorage:
    return storage
