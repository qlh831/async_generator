生成器是python里的黑魔法，本文深度探索生成器异步并发机制，仅import基础库如socket、selectors、collections等，从零实现一个异步并发web框架。

## 什么是生成器
含有`yield`语句的函数是生成器。`yield`这个词的意思有生成、生产、让步等。  
调用了之后的生成器称为生成器对象，例如下面的`gen`
```python
def generator():
    yield 1
    yield from range(9)
    return 100
gen = generator()
```

## 生成器的基础用法
顾名思义，生成器最常见的作用是用来生成值，每次`yield`都生成一个值给上层的调用者。  
通过这种逐步生成值的方式，可以避免一次性将内容load到内存。

生成器的基础用法主要有2种：

### 迭代
通过迭代语法逐个获取生成器yield的值
```python
gen = generator()
for val in gen:
    print(val)
```

### next
通过next方法获取下一个yield值
```python
gen = generator()
while True:
    try:
        val = next(gen)
        print("gen yield", val)
    except StopIteration as e:
        # 后面没有更多的yield了，抛出StopIteration异常
        # 可通过e.value获取生成器return的值
        print("gen return", e.value)
        break
```

## 生成器的高级用法

### send
通过`gen.send(val)`向生成器中传递值
```python
def generator():
    a = yield 1
    yield a + 2
gen = generator()

# 对于还未启动的生成器，第一个send的值必须为None，同时返回第一个yield的值（1）
val = gen.send(None) # val为1

# 此处send的值9传递给了生成器中变量a，同时返回第二个yield的值（9+2）
val = gen.send(9) # val为11
```

### throw
通过`gen.throw(t, v, tb)` 向生成器抛出异常
```python
def generator():
    try:
        a = yield 1 # *
    except TypeError:
        a = 0
    yield a + 2
gen = generator()

# 对于还未启动的生成器，第一个send的值必须为None，同时返回第一个yield的值（1）
# 也可以通过next(gen)来启动第一步，同样返回yield的值（1）
# 第一步不要throw，暂未发现第一步throw的意义
val = gen.send(None) # val为1

# 在上次yield处（*处），抛出异常TypeError
# 生成器内可以捕获异常，因此a获得值0，生成器继续运行，返回a+2即2给val
# 如果生成器内未捕捉异常，则运行中断
val = gen.throw(TypeError, "error message", None) # val为2
```

## 生成器与并发的关系
上面介绍了生成器的基本用法和高级用法，了解到生成器可以被控制着一步一步执行。  

那么生成器如何并发执行呢？

将多个生成器对象保存在列表等容器中，每次遍历这个列表，对每个生成器对象send一次（即推动运行一次），即可达到生成器并行执行的目的。

## 生成器并行调度
### 基本思路
将生成器保存在容器中，循环遍历整个容器，每次遍历对每个生成器对象send一次，如此循环执行，可实现多个生成器函数并行执行的目的。
```python
gen_list = [] # 假设列表里有很多生成器对象
for gen in gen_list:
    gen.send(None)
```

### 运行状态
在上面的代码中，我们向gen中send了None，  
实际上，除了第一次运行生成器可以send值None外，之后的send值必须为上次send返回的值（即上次send后生成器yield的值）,  
因此，需要一种用来保存生成器运行状态的数据结构。保存进程运行状态的数据结构叫PCB（process control block），  
那么在这里，保存生成器运行状态的结构，我们命名为GCB（generator control block）
```python
class GeneratorControlBlock(object):
    def __init__(self, gen, parent=None):
        self.id = id(gen)
        self.gen = gen
        self.val = None
        self.parent = parent
        self.wait_until = 0
```
该结构中包含了生成器id，生成器对象gen，上次yield的值（也是下次send的值）val等。  
每次将val值send到生成器对象中，再将返回结果赋值给val，因为一次一次的send被调度器分离开了，所以需要GCB来保存运行状态。

### 处理sleep
python中的`time.sleep`将线程挂起，等待sleep结束后重新调度。  
生成器的调度也需要有类似的功能，如何实现呢？

在上面GCB的定义中，`wait_util`字段表示在该时间到来之前，当前生成器不参与继续调度，  
当调度器调度到该GCB时，发现当前时间小于wait_util，则跳过本次的send。

### 生成器嵌套
每个生成器被调用后成为生成器对象，生成器对象必须在调度器中被反复的send，才能一步一步的运行完生成器逻辑。  
生成器内调用其它生成器，必须yield出来给调度器，然后在调度器中注册新的生成器对象，才能达到生成器嵌套调用的效果。  
否则生成器没有被调度，里面的逻辑就不会运行。  

yield出来的新生成器在调度器上注册的同时，调用该生成器的父级生成器需要从调度器中注销，等待子生成器运行结束后再重新注册到调度器。

### 异常处理
调度器在生成器对象上send后，生成器里面的逻辑可能出现异常，  
调度器应该捕获这些异常，  
如果该生成器没有父级，则打印堆栈错误，并从调度器上注销该生成器，    
如果该生成器有父级，则用父生成器的throw方法将异常抛出，并注销子生成器，重新注册父生成器。
子生成器抛出的异常，可以在父级生成器中捕获和处理。

无论生成器内发生任何异常，都不应该影响调度器的正常调度。

### 调度器代码实现
调度器代码实现如下：
```python
import time
from collections import OrderedDict

from log import app_log

class _GeneratorControlBlock(object):
    def __init__(self, gen, parent=None):
        self.id = id(gen)
        self.gen = gen
        self.val = None
        self.parent = parent
        self.wait_until = 0

class _GeneratorWait(object):
    def __init__(self, seconds):
        self.seconds = seconds

# 调度器
class _GeneratorScheduler(object):

    def __init__(self):
        self._gcbs = OrderedDict()

    def timestamp(self):
        return time.time()

    def sleep(self, seconds):
        return _GeneratorWait(seconds)

    def _schedule_gen(self, gen, parent=None):
        gcb = _GeneratorControlBlock(gen, parent=parent)
        self._gcbs[gcb.id] = gcb

    def schedule_gen(self, gen):
        # 注册生成器对象
        self._schedule_gen(gen)

    def resume_gcb(self, gcb, val):
        # 注销生成器GCB
        gcb.val = val
        self._gcbs[gcb.id] = gcb

    def run_forever(self):
        while True:
            if not self._gcbs:
                break
            gcbs = tuple(self._gcbs.values())
            for gcb in gcbs:
                if gcb.wait_until > self.timestamp():
                    continue
                try:
                    if isinstance(gcb.val, Exception):
                        _val = gcb.gen.throw(gcb.val)
                    else:
                        _val = gcb.gen.send(gcb.val)
                except StopIteration as e:
                    if gcb.parent is not None:
                        gcb.parent.val = e.value
                        self._gcbs[gcb.parent.id] = gcb.parent
                    del self._gcbs[gcb.id]
                    continue
                except Exception as e:
                    if gcb.parent is not None:
                        gcb.parent.val = e
                        self._gcbs[gcb.parent.id] = gcb.parent
                    else:
                        app_log.exception(e)
                    del self._gcbs[gcb.id]
                    continue
                if isinstance(_val, _GeneratorWait):
                    gcb.wait_until = self.timestamp() + _val.seconds
                    gcb.val = None
                elif hasattr(_val, "_is_future"):
                    del self._gcbs[gcb.id]
                    _val.send(gcb)
                elif hasattr(_val, "__next__"):
                    del self._gcbs[gcb.id]
                    self._schedule_gen(_val, parent=gcb)
                else:
                    gcb.val = _val
```

## 遇到IO怎么办
上面一顿操作，解决了多个生成器并行运行的调度问题。  

生成器在yield处暂停运行，在两个yield之间同步运行，不能卡顿，更不能`time.sleep`让线程挂起。  
那么这样的并行系统里面，如果遇到了IO怎么办？

一般来说，程序遇到IO会被操作系统暂时挂起，等待IO事件完成。  
这种挂起，在生成器并行调度里，和`time.sleep`一样，是不能容忍的，否则整个程序卡死。

这里需借助`selectors`库提供的多路服用功能，达到异步IO的目的。  

`selectors`库基本用法如下：
```python
import selectors
import socket

sel = selectors.DefaultSelector() # 获取当前操作系统的最优selector
sock = socket.socket() # 构造一个socket
sel.register(sock, selectors.EVENT_READ, on_message) # 注册sock可读事件

def on_message(sock, mask): pass

while True:
    events = sel.select(timeout=-1) # 非阻塞获取IO事件
    for key, mask in events: # 逐个处理事件
        callback, sock, mask = key.data, key.fileobj, mask
        callback(sock, mask)
```

## 生成器调度与线程调度的异同点

线程调度和生成器调度的异同点（部分）：
|       | 线程  | 生成器（协程） |
| ---   | ---   | ---   |
| 遇到IO | 线程挂起 | 协程挂起 |
| 遇到sleep | 线程挂起 | 协程挂起 |
| 是否抢占 | 抢占 | 非抢占 |
| x程安全 | 有 | 无 |
| 死锁风险 | 有 | 有 |


## 完整实现

一个完成度还行的web框架（跟tornado有点像）
- 支持生成器并发
- 支持异步IO
- 支持异步http server
- 支持异步http request（简易版）

目录说明：  
.  
|-- README.md       文档  
|-- handler.py      Http请求处理器、路由等  
|-- log.py          日志配置  
|-- main.py         Http服务器入口  
|-- scheduler.py    调度器  
|-- selector.py     异步IO、socket消息处理  
|-- web.py          Http协议解析、封装等  

运行环境：  
运行环境为python3.5+，无任何第三方依赖

启动方式：  
`python3 main.py`启动server，以Http协议监听`localhost:8080`  

链接：https://github.com/qlh831/async_generator
