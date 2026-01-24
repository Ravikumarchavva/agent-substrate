from abc import ABC, abstractmethod

class BaseMemory(ABC):
    @abstractmethod
    def store(self, *args, **kwargs):
        pass

    @abstractmethod
    def retrieve(self, *args, **kwargs):
        pass

    @abstractmethod
    def clear_memory(self):
        pass