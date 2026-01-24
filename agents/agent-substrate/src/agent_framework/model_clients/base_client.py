from abc import ABC, abstractmethod

class BaseModelClient(ABC):
    @abstractmethod
    def on_create(self, *args, **kwargs):
        pass

    @abstractmethod
    def on_create_stream(self, *args, **kwargs):
        pass