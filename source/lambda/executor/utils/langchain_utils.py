from langchain.schema.runnable.base import Runnable
from langchain.schema.runnable import RunnablePassthrough


class LmabdaDict(dict):
    """add lambda to value"""
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        for k in list(self.keys()):
            v = self[k]
            if not callable(v) or not isinstance(v,Runnable):
                self[k] = lambda x:x
        



def create_identity_lambda(keys:list):
    if isinstance(keys,str):
        keys = [keys]
    assert isinstance(keys,list) and keys, keys
     
    assert isinstance(keys[0],str), keys
    
    ret = {k:lambda x:x[k] for k in keys}
    return ret