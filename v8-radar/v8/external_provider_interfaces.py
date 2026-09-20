from __future__ import annotations

from typing import Any,Protocol


class QuoteProvider(Protocol):
    name:str
    def quote(self,code:str)->dict[str,Any]:...


class BrokerProvider(Protocol):
    name:str
    def positions(self)->list[dict[str,Any]]:...
    def account(self)->dict[str,Any]:...


class Level2Provider(Protocol):
    name:str
    def trades(self,code:str)->list[dict[str,Any]]:...
    def order_book(self,code:str)->dict[str,Any]:...


class EventProvider(Protocol):
    name:str
    def unlocks(self,code:str,start_date:str,end_date:str)->list[dict[str,Any]]:...
    def reductions(self,code:str,start_date:str,end_date:str)->list[dict[str,Any]]:...


class UnavailableProvider:
    def __init__(self,name:str,reason:str):self.name=name;self.reason=reason
    def status(self):return {'status':'UNAVAILABLE','provider':self.name,'reason':self.reason}


QMT=UnavailableProvider('QMT','需要券商客户端、账号与本机行情权限')
PTRADE=UnavailableProvider('PTrade','需要券商账号、量化权限和服务端环境')
LEVEL2=UnavailableProvider('Level2','需要正式逐笔成交/逐笔委托授权')
UNLOCK_REDUCTION=UnavailableProvider('UnlockReduction','当前购买清单未证明包含结构化解禁与减持接口')
