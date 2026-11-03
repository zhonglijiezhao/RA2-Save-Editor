# CellClass 拷贝函数 —— 完整反汇编与结构分析

> 2026-11-01 11:40，为追"下车时的空气墙到底是谁写的"而反汇编。
> 目标：`gamemd.exe` 里唯一会写格子 `+0x138` 的代码。

## 0. 定位

全文件搜索 `mov r/m8, r8` 形式的 `[reg+0x138]`（`88 8x 38 01 00 00`），
只有 **2 处**是"写"，且互为镜像：

| 地址 | 指令 | 角色 |
|---|---|---|
| `0x0056605A` / `0x00566066` | `mov dl,[esi+0x138]` → `mov [edi+0x138],dl` | **esi = 源，edi = 目标** |
| `0x0056686C` / `0x00566878` | `mov dl,[edi+0x138]` → `mov [esi+0x138],dl` | **edi = 源，esi = 目标**（反向版） |

两个都是 `CellClass` 的赋值/拷贝（同一个类的两个重载方向）。
区间 `0x565C03 ~ 0x56686C` 共 **799 条指令**。

## 1. 它到底干了什么（结论先行）

**逐字段、原样、无判断地把源格子整块搬给目标格子。**

- 从 `+0xDC` 到 `+0x122` 是**一个个 mov 直接拷**（连 `+0x119/+0x11A/+0x11B` 这种独立字节都单独搬）
- `+0x138`（占用标记）就在其中：**没有任何 test / 判断 / 清零**，读出来就写过去
- `+0x124`、`+0x12C`、`+0x140` 不是直拷，而是**按位重算**（见下）

⇒ **它不是"下车时设置占用"的元凶，但它会把别处的占用原样复制过来。**

只要引擎在某个时刻拿一个"有占用"的格子当模板拷到空地上，
那块空地就立刻变成"登记着被占用、却没有对象"—— 也就是空气墙。

## 2. 关键片段（带注释）

```asm
0x00566033  mov     edx, ecx                              
0x00566035  xor     edx, eax                              
0x00566037  and     edx, 0x10                             
0x0056603a  xor     edx, ecx                              
0x0056603c  mov     dword ptr [edi + 0x12c], edx          
0x00566042  mov     eax, dword ptr [esi + 0x130]          
0x00566048  mov     dword ptr [edi + 0x130], eax          
0x0056604e  mov     ecx, dword ptr [esi + 0x134]          
0x00566054  mov     dword ptr [edi + 0x134], ecx          
0x0056605a  mov     dl, byte ptr [esi + 0x138]              ; 读源格子 +0x138（占用标记，1 字节）
0x00566060  mov     ecx, dword ptr [edi + 0x140]          
0x00566066  mov     byte ptr [edi + 0x138], dl              ; ★写目标格子 +0x138 —— 唯一把占用位搬过去的地方
0x0056606c  mov     eax, dword ptr [esi + 0x13c]          
0x00566072  mov     dword ptr [edi + 0x13c], eax          
0x00566078  mov     edx, dword ptr [esi + 0x140]          
0x0056607e  mov     eax, ecx                              
0x00566080  xor     eax, edx                              
0x00566082  and     eax, 1                                
0x00566085  xor     eax, ecx                              
0x00566087  mov     dword ptr [edi + 0x140], eax          
0x0056608d  mov     edx, dword ptr [esi + 0x140]          
```

## 3. 位运算重算的三个字段

这三处不是直拷，而是"按目标原有的位图改某一位"：

```asm
; 目标 = (目标 XOR 源) AND 掩码 XOR 目标   —— 即"把目标的那一位设成源的样子"
mov  eax, [esi + 0x12c]
xor  ecx, edx
and  ecx, 8              ; ← 第 3 位
mov  [edi + 0x12c], ecx
```

`+0x140` 更啰嗦，`and 1` / `and 2` / `and 4` 一位一位来 —— 编译器把
"逐位合并"展开成了每条指令一位。

## 4. 字段清单

**原样拷贝（37 个字段）**：

```
+0x0DC +0x0E0 +0x0E4 +0x0E8 +0x0EC +0x0F0 +0x0F4 +0x0F8 +0x0FC +0x100 +0x104 +0x108 +0x10A +0x10C +0x10E +0x110 +0x112 +0x114 +0x116 +0x118 +0x119 +0x11A +0x11B +0x11C +0x11D +0x11E +0x11F +0x120 +0x121 +0x122 +0x124 +0x128 +0x12C +0x130 +0x134 +0x138 +0x13C
```

**按位重算（1 个字段）**：+0x140

## 5. 调试建议

在 `0x566066` 下断，命中后立刻看两个值：

```
dd esi+138     ; 源格子的占用标记（非零 = 这格有东西）
dd edi+138     ; 目标格子（即将被覆盖）
```

**只要出现「esi 非零、edi 原本是 0」，就是抓到了空气墙的现场。**
再往上翻调用栈，发命令的那个函数才是真正的漏洞点。

> 注意：这个断点每帧都可能被调用（单位移动时会走格子拷贝），
> 用条件断点过滤一下 —— `[esi+138] != 0 && [edi+138] == 0`。

## 6. 完整反汇编（799 条）

```asm
0x00565c03  nop                                             
0x00565c04  nop                                             
0x00565c05  nop                                             
0x00565c06  nop                                             
0x00565c07  nop                                             
0x00565c08  nop                                             
0x00565c09  nop                                             
0x00565c0a  nop                                             
0x00565c0b  nop                                             
0x00565c0c  nop                                             
0x00565c0d  nop                                             
0x00565c0e  nop                                             
0x00565c0f  nop                                             
0x00565c10  sub     esp, 0x50                               
0x00565c13  push    ebx                                     
0x00565c14  mov     ebx, ecx                                
0x00565c16  mov     ecx, dword ptr [0x87f8e0]               
0x00565c1c  push    ebp                                     
0x00565c1d  mov     eax, dword ptr [ebx + 0xf4]             
0x00565c23  push    esi                                     
0x00565c24  mov     dword ptr [esp + 0x4c], eax             
0x00565c28  mov     eax, dword ptr [0xabd480]               
0x00565c2d  push    edi                                     
0x00565c2e  mov     dword ptr [esp + 0x54], eax             
0x00565c32  mov     eax, dword ptr [0xa8e370]               
0x00565c37  xor     edi, edi                                
0x00565c39  lea     edx, [ecx + ecx + 8]                    
0x00565c3d  xor     esi, esi                                
0x00565c3f  cmp     eax, edi                                
0x00565c41  mov     dword ptr [esp + 0x38], edi             
0x00565c45  mov     dword ptr [esp + 0x48], edi             
0x00565c49  mov     dword ptr [esp + 0x4c], edx             
0x00565c4d  jle     0x565c6b                                
0x00565c4f  mov     ecx, dword ptr [0xa8e364]               
0x00565c55  push    edi                                     
0x00565c56  mov     ecx, dword ptr [ecx + esi*4]            
0x00565c59  mov     edx, dword ptr [ecx]                    
0x00565c5b  call    dword ptr [edx + 0x124]                 
0x00565c61  mov     eax, dword ptr [0xa8e370]               
0x00565c66  inc     esi                                     
0x00565c67  cmp     esi, eax                                
0x00565c69  jl      0x565c4f                                
0x00565c6b  cmp     dword ptr [ebx + 0x140], edi            
0x00565c71  jle     0x565ce5                                
0x00565c73  mov     esi, edi                                
0x00565c75  and     esi, 0x800001ff                         
0x00565c7b  jns     0x565c85                                
0x00565c7d  dec     esi                                     
0x00565c7e  or      esi, 0xfffffe00                         
0x00565c84  inc     esi                                     
0x00565c85  mov     eax, edi                                
0x00565c87  cdq                                             
0x00565c88  and     edx, 0x1ff                              
0x00565c8e  add     eax, edx                                
0x00565c90  mov     edx, dword ptr [ebx + 0xf4]             
0x00565c96  sar     eax, 9                                  
0x00565c99  movsx   esi, si                                 
0x00565c9c  movsx   eax, ax                                 
0x00565c9f  lea     ecx, [esi + eax]                        
0x00565ca2  cmp     ecx, edx                                
0x00565ca4  jle     0x565cc1                                
0x00565ca6  mov     ebp, esi                                
0x00565ca8  sub     ebp, eax                                
0x00565caa  cmp     ebp, edx                                
0x00565cac  jge     0x565cc1                                
0x00565cae  sub     eax, esi                                
0x00565cb0  cmp     eax, edx                                
0x00565cb2  jge     0x565cc1                                
0x00565cb4  mov     eax, dword ptr [ebx + 0xf8]             
0x00565cba  lea     edx, [edx + eax*2]                      
0x00565cbd  cmp     ecx, edx                                
0x00565cbf  jle     0x565cd8                                
0x00565cc1  mov     eax, dword ptr [ebx + 0x13c]            
0x00565cc7  mov     eax, dword ptr [eax + edi*4]            
0x00565cca  test    eax, eax                                
0x00565ccc  je      0x565cd8                                
0x00565cce  mov     cl, byte ptr [esp + 0x6c]               
0x00565cd2  mov     byte ptr [eax + 0x11b], cl              
0x00565cd8  mov     eax, dword ptr [ebx + 0x140]            
0x00565cde  inc     edi                                     
0x00565cdf  cmp     edi, eax                                
0x00565ce1  jl      0x565c73                                
0x00565ce3  xor     edi, edi                                
0x00565ce5  mov     al, byte ptr [esp + 0x68]               
0x00565ce9  mov     dword ptr [esp + 0x34], edi             
0x00565ced  test    al, al                                  
0x00565cef  jne     0x5662ac                                
0x00565cf5  mov     eax, dword ptr [esp + 0x4c]             
0x00565cf9  imul    eax, dword ptr [esp + 0x50]             
0x00565cfe  lea     edx, [eax + eax*4]                      
0x00565d01  lea     eax, [eax + edx*8]                      
0x00565d04  shl     eax, 3                                  
0x00565d07  push    eax                                     
0x00565d08  call    0x7c8e17                                
0x00565d0d  mov     dword ptr [esp + 0x3c], eax             
0x00565d11  mov     dword ptr [esp + 0x4c], eax             
0x00565d15  mov     eax, dword ptr [ebx + 0xf4]             
0x00565d1b  mov     dword ptr [ebx + 0x10c], 1              
0x00565d25  mov     dword ptr [ebx + 0x110], eax            
0x00565d2b  add     esp, 4                                  
0x00565d2e  lea     ecx, [eax - 1]                          
0x00565d31  mov     dword ptr [ebx + 0x114], ecx            
0x00565d37  mov     edx, dword ptr [ebx + 0x13c]            
0x00565d3d  shl     eax, 0xb                                
0x00565d40  mov     ecx, ebx                                
0x00565d42  lea     eax, [eax + edx + 4]                    
0x00565d46  mov     dword ptr [ebx + 0x118], eax            
0x00565d4c  call    0x578290                                
0x00565d51  mov     esi, eax                                
0x00565d53  test    esi, esi                                
0x00565d55  mov     ecx, dword ptr [esi + 0x24]             
0x00565d58  mov     dword ptr [esp + 0x54], ecx             
0x00565d5c  je      0x5662ac                                
0x00565d62  mov     edx, dword ptr [esp + 0x38]             
0x00565d66  lea     ebp, [edx + 0x28]                       
0x00565d69  lea     edi, [ebp - 0x28]                       
0x00565d6c  push    esi                                     
0x00565d6d  mov     ecx, edi                                
0x00565d6f  call    0x588c10                                
0x00565d74  mov     eax, dword ptr [esi + 0x24]             
0x00565d77  mov     dword ptr [esp + 0x2c], 0x18            
0x00565d7f  mov     dword ptr [ebp - 4], eax                
0x00565d82  mov     ecx, dword ptr [esi + 0x28]             
0x00565d85  mov     dword ptr [ebp], ecx                    
0x00565d88  mov     edx, dword ptr [esi + 0x2c]             
0x00565d8b  mov     dword ptr [ebp + 4], edx                
0x00565d8e  mov     eax, dword ptr [esi + 0x30]             
0x00565d91  mov     dword ptr [ebp + 8], eax                
0x00565d94  mov     ecx, dword ptr [esi + 0x34]             
0x00565d97  mov     dword ptr [ebp + 0xc], ecx              
0x00565d9a  mov     edx, dword ptr [esi + 0x38]             
0x00565d9d  mov     dword ptr [ebp + 0x10], edx             
0x00565da0  mov     eax, dword ptr [esi + 0x3c]             
0x00565da3  mov     dword ptr [ebp + 0x14], eax             
0x00565da6  mov     ecx, dword ptr [esi + 0x40]             
0x00565da9  mov     dword ptr [ebp + 0x18], ecx             
0x00565dac  mov     edx, dword ptr [esi + 0x44]             
0x00565daf  mov     dword ptr [ebp + 0x1c], edx             
0x00565db2  mov     eax, dword ptr [esi + 0x48]             
0x00565db5  mov     dword ptr [ebp + 0x20], eax             
0x00565db8  mov     ecx, dword ptr [esi + 0x4c]             
0x00565dbb  mov     dword ptr [ebp + 0x24], ecx             
0x00565dbe  mov     edx, dword ptr [esi + 0x50]             
0x00565dc1  mov     dword ptr [ebp + 0x28], edx             
0x00565dc4  mov     eax, dword ptr [esi + 0x54]             
0x00565dc7  mov     dword ptr [ebp + 0x2c], eax             
0x00565dca  mov     ecx, dword ptr [esi + 0x58]             
0x00565dcd  mov     dword ptr [ebp + 0x30], ecx             
0x00565dd0  mov     edx, dword ptr [esi + 0x5c]             
0x00565dd3  mov     dword ptr [ebp + 0x34], edx             
0x00565dd6  mov     eax, dword ptr [esi + 0x60]             
0x00565dd9  mov     dword ptr [ebp + 0x38], eax             
0x00565ddc  mov     ecx, dword ptr [esi + 0x64]             
0x00565ddf  lea     edx, [esi + 0x68]                       
0x00565de2  mov     dword ptr [ebp + 0x3c], ecx             
0x00565de5  lea     eax, [ebp + 0x40]                       
0x00565de8  mov     ecx, dword ptr [edx]                    
0x00565dea  mov     dword ptr [eax], ecx                    
0x00565dec  mov     ecx, dword ptr [edx + 4]                
0x00565def  mov     dword ptr [eax + 4], ecx                
0x00565df2  mov     ecx, dword ptr [edx + 8]                
0x00565df5  mov     dword ptr [eax + 8], ecx                
0x00565df8  mov     ecx, esi                                
0x00565dfa  mov     edx, dword ptr [edx + 0xc]              
0x00565dfd  sub     ecx, edi                                
0x00565dff  mov     dword ptr [eax + 0xc], edx              
0x00565e02  mov     eax, dword ptr [esi + 0x78]             
0x00565e05  mov     dword ptr [ebp + 0x50], eax             
0x00565e08  lea     eax, [ebp + 0x54]                       
0x00565e0b  mov     dx, word ptr [ecx + eax]                
0x00565e0f  mov     word ptr [eax], dx                      
0x00565e12  mov     edx, dword ptr [esp + 0x2c]             
0x00565e16  add     eax, 2                                  
0x00565e19  dec     edx                                     
0x00565e1a  mov     dword ptr [esp + 0x2c], edx             
0x00565e1e  jne     0x565e0b                                
0x00565e20  lea     eax, [edi + 0xac]                       
0x00565e26  mov     dword ptr [esp + 0x2c], 0x18            
0x00565e2e  mov     dx, word ptr [eax + ecx]                
0x00565e32  mov     word ptr [eax], dx                      
0x00565e35  mov     edx, dword ptr [esp + 0x2c]             
0x00565e39  add     eax, 2                                  
0x00565e3c  dec     edx                                     
0x00565e3d  mov     dword ptr [esp + 0x2c], edx             
0x00565e41  jne     0x565e2e                                
0x00565e43  mov     eax, dword ptr [esi + 0xdc]             
0x00565e49  mov     dword ptr [edi + 0xdc], eax             
0x00565e4f  mov     ecx, dword ptr [esi + 0xe0]             
0x00565e55  mov     dword ptr [edi + 0xe0], ecx             
0x00565e5b  mov     edx, dword ptr [esi + 0xe4]             
0x00565e61  mov     dword ptr [edi + 0xe4], edx             
0x00565e67  mov     eax, dword ptr [esi + 0xe8]             
0x00565e6d  mov     dword ptr [edi + 0xe8], eax             
0x00565e73  mov     ecx, dword ptr [esi + 0xec]             
0x00565e79  mov     dword ptr [edi + 0xec], ecx             
0x00565e7f  mov     edx, dword ptr [esi + 0xf0]             
0x00565e85  mov     dword ptr [edi + 0xf0], edx             
0x00565e8b  mov     eax, dword ptr [esi + 0xf4]             
0x00565e91  mov     dword ptr [edi + 0xf4], eax             
0x00565e97  mov     ecx, dword ptr [esi + 0xf8]             
0x00565e9d  mov     dword ptr [edi + 0xf8], ecx             
0x00565ea3  mov     edx, dword ptr [esi + 0xfc]             
0x00565ea9  mov     dword ptr [edi + 0xfc], edx             
0x00565eaf  mov     eax, dword ptr [esi + 0x100]            
0x00565eb5  mov     dword ptr [edi + 0x100], eax            
0x00565ebb  mov     ecx, dword ptr [esi + 0x104]            
0x00565ec1  mov     dword ptr [edi + 0x104], ecx            
0x00565ec7  mov     dx, word ptr [esi + 0x108]              
0x00565ece  mov     word ptr [edi + 0x108], dx              
0x00565ed5  mov     ax, word ptr [esi + 0x10a]              
0x00565edc  mov     word ptr [edi + 0x10a], ax              
0x00565ee3  mov     cx, word ptr [esi + 0x10c]              
0x00565eea  mov     word ptr [edi + 0x10c], cx              
0x00565ef1  mov     dx, word ptr [esi + 0x10e]              
0x00565ef8  mov     word ptr [edi + 0x10e], dx              
0x00565eff  mov     ax, word ptr [esi + 0x110]              
0x00565f06  mov     word ptr [edi + 0x110], ax              
0x00565f0d  mov     cx, word ptr [esi + 0x112]              
0x00565f14  mov     word ptr [edi + 0x112], cx              
0x00565f1b  mov     dx, word ptr [esi + 0x114]              
0x00565f22  mov     word ptr [edi + 0x114], dx              
0x00565f29  mov     ax, word ptr [esi + 0x116]              
0x00565f30  mov     word ptr [edi + 0x116], ax              
0x00565f37  mov     cl, byte ptr [esi + 0x118]              
0x00565f3d  mov     byte ptr [edi + 0x118], cl              
0x00565f43  mov     dl, byte ptr [esi + 0x119]              
0x00565f49  mov     byte ptr [edi + 0x119], dl              
0x00565f4f  mov     al, byte ptr [esi + 0x11a]              
0x00565f55  mov     byte ptr [edi + 0x11a], al              
0x00565f5b  mov     cl, byte ptr [esi + 0x11b]              
0x00565f61  mov     byte ptr [edi + 0x11b], cl              
0x00565f67  mov     dl, byte ptr [esi + 0x11c]              
0x00565f6d  mov     byte ptr [edi + 0x11c], dl              
0x00565f73  mov     al, byte ptr [esi + 0x11d]              
0x00565f79  mov     byte ptr [edi + 0x11d], al              
0x00565f7f  mov     cl, byte ptr [esi + 0x11e]              
0x00565f85  mov     byte ptr [edi + 0x11e], cl              
0x00565f8b  mov     dl, byte ptr [esi + 0x11f]              
0x00565f91  mov     byte ptr [edi + 0x11f], dl              
0x00565f97  mov     al, byte ptr [esi + 0x120]              
0x00565f9d  mov     byte ptr [edi + 0x120], al              
0x00565fa3  mov     cl, byte ptr [esi + 0x121]              
0x00565fa9  mov     byte ptr [edi + 0x121], cl              
0x00565faf  mov     dl, byte ptr [esi + 0x122]              
0x00565fb5  mov     byte ptr [edi + 0x122], dl              
0x00565fbb  mov     eax, dword ptr [esi + 0x124]            
0x00565fc1  mov     dword ptr [edi + 0x124], eax            
0x00565fc7  mov     ecx, dword ptr [esi + 0x128]            
0x00565fcd  mov     dword ptr [edi + 0x128], ecx            
0x00565fd3  mov     ecx, dword ptr [edi + 0x12c]            
0x00565fd9  mov     edx, dword ptr [esi + 0x12c]            
0x00565fdf  mov     eax, ecx                                
0x00565fe1  xor     eax, edx                                
0x00565fe3  and     eax, 1                                  
0x00565fe6  xor     eax, ecx                                
0x00565fe8  mov     dword ptr [edi + 0x12c], eax            
0x00565fee  mov     edx, dword ptr [esi + 0x12c]            
0x00565ff4  mov     ecx, eax                                
0x00565ff6  xor     ecx, edx                                
0x00565ff8  and     ecx, 2                                  
0x00565ffb  xor     ecx, eax                                
0x00565ffd  mov     dword ptr [edi + 0x12c], ecx            
0x00566003  mov     edx, dword ptr [esi + 0x12c]            
0x00566009  mov     eax, ecx                                
0x0056600b  xor     eax, edx                                
0x0056600d  and     eax, 4                                  
0x00566010  xor     eax, ecx                                
0x00566012  mov     dword ptr [edi + 0x12c], eax            
0x00566018  mov     edx, dword ptr [esi + 0x12c]            
0x0056601e  mov     ecx, eax                                
0x00566020  xor     ecx, edx                                
0x00566022  and     ecx, 8                                  
0x00566025  xor     ecx, eax                                
0x00566027  mov     dword ptr [edi + 0x12c], ecx            
0x0056602d  mov     eax, dword ptr [esi + 0x12c]            
0x00566033  mov     edx, ecx                                
0x00566035  xor     edx, eax                                
0x00566037  and     edx, 0x10                               
0x0056603a  xor     edx, ecx                                
0x0056603c  mov     dword ptr [edi + 0x12c], edx            
0x00566042  mov     eax, dword ptr [esi + 0x130]            
0x00566048  mov     dword ptr [edi + 0x130], eax            
0x0056604e  mov     ecx, dword ptr [esi + 0x134]            
0x00566054  mov     dword ptr [edi + 0x134], ecx            
0x0056605a  mov     dl, byte ptr [esi + 0x138]                 ; <<<< +0x138
0x00566060  mov     ecx, dword ptr [edi + 0x140]            
0x00566066  mov     byte ptr [edi + 0x138], dl                 ; <<<< +0x138
0x0056606c  mov     eax, dword ptr [esi + 0x13c]            
0x00566072  mov     dword ptr [edi + 0x13c], eax            
0x00566078  mov     edx, dword ptr [esi + 0x140]            
0x0056607e  mov     eax, ecx                                
0x00566080  xor     eax, edx                                
0x00566082  and     eax, 1                                  
0x00566085  xor     eax, ecx                                
0x00566087  mov     dword ptr [edi + 0x140], eax            
0x0056608d  mov     edx, dword ptr [esi + 0x140]            
0x00566093  mov     ecx, eax                                
0x00566095  xor     ecx, edx                                
0x00566097  and     ecx, 2                                  
0x0056609a  xor     ecx, eax                                
0x0056609c  mov     dword ptr [edi + 0x140], ecx            
0x005660a2  mov     edx, dword ptr [esi + 0x140]            
0x005660a8  mov     eax, ecx                                
0x005660aa  xor     eax, edx                                
0x005660ac  and     eax, 4                                  
0x005660af  xor     eax, ecx                                
0x005660b1  mov     dword ptr [edi + 0x140], eax            
0x005660b7  mov     edx, dword ptr [esi + 0x140]            
0x005660bd  mov     ecx, eax                                
0x005660bf  xor     ecx, edx                                
0x005660c1  and     ecx, 8                                  
0x005660c4  xor     ecx, eax                                
0x005660c6  mov     dword ptr [edi + 0x140], ecx            
0x005660cc  mov     edx, dword ptr [esi + 0x140]            
0x005660d2  mov     eax, ecx                                
0x005660d4  xor     eax, edx                                
0x005660d6  and     eax, 0x10                               
0x005660d9  xor     eax, ecx                                
0x005660db  mov     dword ptr [edi + 0x140], eax            
0x005660e1  mov     edx, dword ptr [esi + 0x140]            
0x005660e7  mov     ecx, eax                                
0x005660e9  xor     ecx, edx                                
0x005660eb  and     ecx, 0x20                               
0x005660ee  xor     ecx, eax                                
0x005660f0  mov     dword ptr [edi + 0x140], ecx            
0x005660f6  mov     edx, dword ptr [esi + 0x140]            
0x005660fc  mov     eax, ecx                                
0x005660fe  xor     eax, edx                                
0x00566100  and     eax, 0x40                               
0x00566103  xor     eax, ecx                                
0x00566105  mov     dword ptr [edi + 0x140], eax            
0x0056610b  mov     edx, dword ptr [esi + 0x140]            
0x00566111  mov     ecx, eax                                
0x00566113  xor     ecx, edx                                
0x00566115  and     ecx, 0x80                               
0x0056611b  xor     ecx, eax                                
0x0056611d  mov     dword ptr [edi + 0x140], ecx            
0x00566123  mov     edx, dword ptr [esi + 0x140]            
0x00566129  mov     eax, ecx                                
0x0056612b  xor     eax, edx                                
0x0056612d  and     eax, 0x100                              
0x00566132  xor     eax, ecx                                
0x00566134  mov     dword ptr [edi + 0x140], eax            
0x0056613a  mov     edx, dword ptr [esi + 0x140]            
0x00566140  mov     ecx, eax                                
0x00566142  xor     ecx, edx                                
0x00566144  and     ecx, 0x200                              
0x0056614a  xor     ecx, eax                                
0x0056614c  mov     dword ptr [edi + 0x140], ecx            
0x00566152  mov     edx, dword ptr [esi + 0x140]            
0x00566158  mov     eax, ecx                                
0x0056615a  xor     eax, edx                                
0x0056615c  and     eax, 0x400                              
0x00566161  xor     eax, ecx                                
0x00566163  mov     dword ptr [edi + 0x140], eax            
0x00566169  mov     edx, dword ptr [esi + 0x140]            
0x0056616f  mov     ecx, eax                                
0x00566171  xor     ecx, edx                                
0x00566173  and     ecx, 0x800                              
0x00566179  xor     ecx, eax                                
0x0056617b  mov     dword ptr [edi + 0x140], ecx            
0x00566181  mov     edx, dword ptr [esi + 0x140]            
0x00566187  mov     eax, ecx                                
0x00566189  xor     eax, edx                                
0x0056618b  and     eax, 0x1000                             
0x00566190  xor     eax, ecx                                
0x00566192  mov     dword ptr [edi + 0x140], eax            
0x00566198  mov     edx, dword ptr [esi + 0x140]            
0x0056619e  mov     ecx, eax                                
0x005661a0  xor     ecx, edx                                
0x005661a2  and     ecx, 0x2000                             
0x005661a8  xor     ecx, eax                                
0x005661aa  mov     dword ptr [edi + 0x140], ecx            
0x005661b0  mov     edx, dword ptr [esi + 0x140]            
0x005661b6  mov     eax, ecx                                
0x005661b8  xor     eax, edx                                
0x005661ba  and     eax, 0x4000                             
0x005661bf  xor     eax, ecx                                
0x005661c1  mov     dword ptr [edi + 0x140], eax            
0x005661c7  mov     edx, dword ptr [esi + 0x140]            
0x005661cd  mov     ecx, eax                                
0x005661cf  xor     ecx, edx                                
0x005661d1  and     ecx, 0x8000                             
0x005661d7  xor     ecx, eax                                
0x005661d9  mov     dword ptr [edi + 0x140], ecx            
0x005661df  mov     edx, dword ptr [esi + 0x140]            
0x005661e5  mov     eax, ecx                                
0x005661e7  xor     eax, edx                                
0x005661e9  and     eax, 0x10000                            
0x005661ee  xor     eax, ecx                                
0x005661f0  mov     dword ptr [edi + 0x140], eax            
0x005661f6  mov     edx, dword ptr [esi + 0x140]            
0x005661fc  mov     ecx, eax                                
0x005661fe  xor     ecx, edx                                
0x00566200  and     ecx, 0x20000                            
0x00566206  xor     ecx, eax                                
0x00566208  mov     dword ptr [edi + 0x140], ecx            
0x0056620e  mov     edx, dword ptr [esi + 0x140]            
0x00566214  mov     eax, ecx                                
0x00566216  xor     eax, edx                                
0x00566218  and     eax, 0x40000                            
0x0056621d  xor     eax, ecx                                
0x0056621f  mov     dword ptr [edi + 0x140], eax            
0x00566225  mov     edx, dword ptr [esi + 0x140]            
0x0056622b  mov     ecx, eax                                
0x0056622d  xor     ecx, edx                                
0x0056622f  and     ecx, 0x80000                            
0x00566235  xor     ecx, eax                                
0x00566237  mov     dword ptr [edi + 0x140], ecx            
0x0056623d  mov     edx, dword ptr [esi + 0x140]            
0x00566243  mov     eax, ecx                                
0x00566245  xor     eax, edx                                
0x00566247  and     eax, 0x100000                           
0x0056624c  xor     eax, ecx                                
0x0056624e  mov     dword ptr [edi + 0x140], eax            
0x00566254  mov     edx, dword ptr [esi + 0x140]            
0x0056625a  mov     ecx, eax                                
0x0056625c  xor     ecx, edx                                
0x0056625e  and     ecx, 0x200000                           
0x00566264  xor     ecx, eax                                
0x00566266  mov     dword ptr [edi + 0x140], ecx            
0x0056626c  mov     eax, dword ptr [esi + 0x140]            
0x00566272  mov     edx, ecx                                
0x00566274  xor     edx, eax                                
0x00566276  and     edx, 0x400000                           
0x0056627c  xor     edx, ecx                                
0x0056627e  xor     eax, eax                                
0x00566280  mov     dword ptr [edi + 0x140], edx            
0x00566286  mov     dword ptr [ebp + 4], eax                
0x00566289  mov     ecx, ebx                                
0x0056628b  mov     dword ptr [ebp + 8], eax                
0x0056628e  call    0x578290                                
0x00566293  mov     edx, dword ptr [esp + 0x34]             
0x00566297  mov     esi, eax                                
0x00566299  inc     edx                                     
0x0056629a  add     ebp, 0x148                              
0x005662a0  test    esi, esi                                
0x005662a2  mov     dword ptr [esp + 0x34], edx             
0x005662a6  jne     0x565d69                                
0x005662ac  mov     ecx, dword ptr [esp + 0x64]             
0x005662b0  lea     eax, [ebx + 0xec]                       
0x005662b6  mov     edx, ecx                                
0x005662b8  mov     esi, eax                                
0x005662ba  xor     ebp, ebp                                
0x005662bc  mov     edi, dword ptr [edx]                    
0x005662be  mov     dword ptr [esi], edi                    
0x005662c0  mov     edi, dword ptr [edx + 4]                
0x005662c3  mov     dword ptr [esi + 4], edi                
0x005662c6  mov     edi, dword ptr [edx + 8]                
0x005662c9  mov     dword ptr [esi + 8], edi                
0x005662cc  mov     edx, dword ptr [edx + 0xc]              
0x005662cf  mov     dword ptr [esi + 0xc], edx              
0x005662d2  mov     edx, dword ptr [ebx + 0xf4]             
0x005662d8  mov     dword ptr [eax], ebp                    
0x005662da  mov     eax, 0x3f                               
0x005662df  cmp     edx, eax                                
0x005662e1  mov     dword ptr [ebx + 0xf0], ebp             
0x005662e7  jg      0x5662f1                                
0x005662e9  cmp     dword ptr [ebx + 0xf8], eax             
0x005662ef  jle     0x5662fb                                
0x005662f1  mov     dword ptr [0x880c74], 2                 
0x005662fb  mov     eax, dword ptr [0x87f8dc]               
0x00566300  mov     edx, dword ptr [0x87f8e0]               
0x00566306  mov     dword ptr [0xabed04], eax               
0x0056630b  mov     dword ptr [esp + 0x3c], ebp             
0x0056630f  lea     eax, [eax + edx*2]                      
0x00566312  mov     edx, 1                                  
0x00566317  mov     dword ptr [0xabed08], eax               
0x0056631c  mov     dword ptr [ebx + 0x124], edx            
0x00566322  mov     dword ptr [ebx + 0x128], edx            
0x00566328  mov     eax, dword ptr [ecx + 0xc]              
0x0056632b  mov     ecx, dword ptr [ecx + 8]                
0x0056632e  lea     eax, [eax + ecx - 1]                    
0x00566332  mov     dword ptr [ebx + 0x12c], eax            
0x00566338  mov     dword ptr [ebx + 0x130], eax            
0x0056633e  lea     eax, [eax + eax + 2]                    
0x00566342  test    eax, eax                                
0x00566344  jle     0x56645c                                
0x0056634a  mov     dword ptr [esp + 0x2c], ebp             
0x0056634e  mov     ecx, dword ptr [ebx + 0x12c]            
0x00566354  xor     esi, esi                                
0x00566356  add     ecx, 2                                  
0x00566359  test    ecx, ecx                                
0x0056635b  jle     0x566432                                
0x00566361  movsx   edx, bp                                 
0x00566364  mov     dword ptr [esp + 0x14], edx             
0x00566368  mov     eax, dword ptr [esp + 0x14]             
0x0056636c  mov     word ptr [esp + 0x20], si               
0x00566371  movsx   ecx, si                                 
0x00566374  mov     word ptr [esp + 0x22], bp               
0x00566379  lea     edx, [ecx + eax]                        
0x0056637c  mov     eax, dword ptr [ebx + 0xf4]             
0x00566382  cmp     edx, eax                                
0x00566384  jle     0x566420                                
0x0056638a  mov     edi, ecx                                
0x0056638c  sub     edi, dword ptr [esp + 0x14]             
0x00566390  cmp     edi, eax                                
0x00566392  jge     0x566420                                
0x00566398  mov     edi, dword ptr [esp + 0x14]             
0x0056639c  sub     edi, ecx                                
0x0056639e  cmp     edi, eax                                
0x005663a0  jge     0x566420                                
0x005663a2  mov     ecx, dword ptr [ebx + 0xf8]             
0x005663a8  lea     eax, [eax + ecx*2]                      
0x005663ab  cmp     edx, eax                                
0x005663ad  jg      0x566420                                
0x005663af  mov     ecx, dword ptr [esp + 0x2c]             
0x005663b3  mov     edx, dword ptr [ebx + 0x13c]            
0x005663b9  lea     edi, [ecx + esi]                        
0x005663bc  mov     ecx, dword ptr [edx + edi*4]            
0x005663bf  test    ecx, ecx                                
0x005663c1  jne     0x5663fc                                
0x005663c3  push    0x148                                   
0x005663c8  call    0x7c8e17                                
0x005663cd  add     esp, 4                                  
0x005663d0  test    eax, eax                                
0x005663d2  je      0x5663df                                
0x005663d4  mov     ecx, eax                                
0x005663d6  call    0x47bbf0                                
0x005663db  mov     ebp, eax                                
0x005663dd  jmp     0x5663e1                                
0x005663df  xor     ebp, ebp                                
0x005663e1  lea     eax, [esp + 0x20]                       
0x005663e5  mov     ecx, ebp                                
0x005663e7  push    eax                                     
0x005663e8  call    0x485240                                
0x005663ed  mov     ecx, dword ptr [ebx + 0x13c]            
0x005663f3  mov     dword ptr [ecx + edi*4], ebp            
0x005663f6  mov     ebp, dword ptr [esp + 0x3c]             
0x005663fa  jmp     0x56640d                                
0x005663fc  call    0x47bbf0                                
0x00566401  lea     edx, [esp + 0x20]                       
0x00566405  mov     ecx, eax                                
0x00566407  push    edx                                     
0x00566408  call    0x485240                                
0x0056640d  mov     eax, dword ptr [ebx + 0x13c]            
0x00566413  mov     dl, byte ptr [esp + 0x6c]               
0x00566417  mov     ecx, dword ptr [eax + edi*4]            
0x0056641a  mov     byte ptr [ecx + 0x11b], dl              
0x00566420  mov     eax, dword ptr [ebx + 0x12c]            
0x00566426  inc     esi                                     
0x00566427  add     eax, 2                                  
0x0056642a  cmp     esi, eax                                
0x0056642c  jl      0x566368                                
0x00566432  mov     ecx, dword ptr [esp + 0x2c]             
0x00566436  inc     ebp                                     
0x00566437  add     ecx, 0x200                              
0x0056643d  mov     dword ptr [esp + 0x3c], ebp             
0x00566441  mov     dword ptr [esp + 0x2c], ecx             
0x00566445  mov     ecx, dword ptr [ebx + 0x130]            
0x0056644b  lea     edx, [ecx + ecx + 2]                    
0x0056644f  cmp     ebp, edx                                
0x00566451  jl      0x56634e                                
0x00566457  mov     edx, 1                                  
0x0056645c  mov     al, byte ptr [esp + 0x68]               
0x00566460  mov     byte ptr [esp + 0x13], 0                
0x00566465  test    al, al                                  
0x00566467  jne     0x5670d8                                
0x0056646d  mov     eax, dword ptr [ebx + 0xf4]             
0x00566473  mov     dword ptr [ebx + 0x10c], edx            
0x00566479  mov     dword ptr [ebx + 0x110], eax            
0x0056647f  lea     ecx, [eax - 1]                          
0x00566482  mov     dword ptr [ebx + 0x114], ecx            
0x00566488  mov     edx, dword ptr [ebx + 0x13c]            
0x0056648e  shl     eax, 0xb                                
0x00566491  mov     ecx, ebx                                
0x00566493  lea     eax, [eax + edx + 4]                    
0x00566497  mov     dword ptr [ebx + 0x118], eax            
0x0056649d  call    0x578290                                
0x005664a2  mov     ecx, dword ptr [eax + 0x24]             
0x005664a5  mov     edx, dword ptr [esp + 0x64]             
0x005664a9  mov     ebp, dword ptr [esp + 0x54]             
0x005664ad  mov     edi, dword ptr [esp + 0x56]             
0x005664b1  mov     eax, dword ptr [edx + 4]                
0x005664b4  mov     edx, dword ptr [edx]                    
0x005664b6  mov     dword ptr [esp + 0x14], ecx             
0x005664ba  sub     ecx, ebp                                
0x005664bc  mov     word ptr [esp + 0x3c], cx               
0x005664c1  mov     ecx, dword ptr [esp + 0x16]             
0x005664c5  lea     esi, [edx + eax]                        
0x005664c8  sub     eax, edx                                
0x005664ca  sub     ecx, edi                                
0x005664cc  mov     word ptr [esp + 0x20], si               
0x005664d1  mov     word ptr [esp + 0x22], ax               
0x005664d6  mov     esi, dword ptr [esp + 0x20]             
0x005664da  mov     word ptr [esp + 0x3e], cx               
0x005664df  mov     edx, dword ptr [esp + 0x3c]             
0x005664e3  mov     eax, dword ptr [esp + 0x3e]             
0x005664e7  sub     edx, esi                                
0x005664e9  mov     word ptr [esp + 0x68], dx               
0x005664ee  mov     edx, dword ptr [esp + 0x22]             
0x005664f2  sub     eax, edx                                
0x005664f4  mov     word ptr [esp + 0x6a], ax               
0x005664f9  mov     eax, dword ptr [esp + 0x34]             
0x005664fd  mov     ecx, dword ptr [esp + 0x68]             
0x00566501  test    eax, eax                                
0x00566503  mov     dword ptr [esp + 0x2c], ecx             
0x00566507  jle     0x566ab7                                
0x0056650d  mov     edx, dword ptr [esp + 0x48]             
0x00566511  mov     dword ptr [esp + 0x3c], eax             
0x00566515  lea     ebp, [edx + 0x24]                       
0x00566518  mov     eax, dword ptr [ebp]                    
0x0056651b  mov     ecx, dword ptr [esp + 0x2c]             
0x0056651f  mov     edx, dword ptr [esp + 0x2e]             
0x00566523  mov     dword ptr [esp + 0x54], eax             
0x00566527  add     ecx, eax                                
0x00566529  mov     eax, dword ptr [esp + 0x56]             
0x0056652d  add     eax, edx                                
0x0056652f  mov     word ptr [esp + 0x68], cx               
0x00566534  mov     word ptr [esp + 0x6a], ax               
0x00566539  lea     edi, [ebp - 0x24]                       
0x0056653c  movsx   eax, ax                                 
0x0056653f  movsx   ecx, cx                                 
0x00566542  shl     eax, 9                                  
0x00566545  add     eax, ecx                                
0x00566547  js      0x566561                                
0x00566549  cmp     eax, 0x40000                            
0x0056654e  jge     0x566561                                
0x00566550  mov     edx, dword ptr [ebx + 0x13c]            
0x00566556  mov     eax, dword ptr [edx + eax*4]            
0x00566559  test    eax, eax                                
0x0056655b  je      0x566561                                
0x0056655d  mov     esi, eax                                
0x0056655f  jmp     0x56656f                                
0x00566561  mov     eax, dword ptr [esp + 0x68]             
0x00566565  mov     esi, 0xabdc50                           
0x0056656a  mov     dword ptr [0xabdc74], eax               
0x0056656f  mov     eax, dword ptr [esi + 0x24]             
0x00566572  test    esi, esi                                
0x00566574  mov     dword ptr [esp + 0x34], eax             
0x00566578  je      0x566aa2                                
0x0056657e  push    edi                                     
0x0056657f  mov     ecx, esi                                
0x00566581  call    0x588c10                                
0x00566586  mov     ecx, dword ptr [ebp]                    
0x00566589  mov     dword ptr [esp + 0x48], 0x18            
0x00566591  mov     dword ptr [esi + 0x24], ecx             
0x00566594  mov     edx, dword ptr [ebp + 4]                
0x00566597  mov     dword ptr [esi + 0x28], edx             
0x0056659a  mov     eax, dword ptr [ebp + 8]                
0x0056659d  mov     dword ptr [esi + 0x2c], eax             
0x005665a0  mov     ecx, dword ptr [ebp + 0xc]              
0x005665a3  mov     dword ptr [esi + 0x30], ecx             
0x005665a6  mov     edx, dword ptr [ebp + 0x10]             
0x005665a9  mov     dword ptr [esi + 0x34], edx             
0x005665ac  mov     eax, dword ptr [ebp + 0x14]             
0x005665af  mov     dword ptr [esi + 0x38], eax             
0x005665b2  mov     ecx, dword ptr [ebp + 0x18]             
0x005665b5  mov     dword ptr [esi + 0x3c], ecx             
0x005665b8  mov     edx, dword ptr [ebp + 0x1c]             
0x005665bb  mov     dword ptr [esi + 0x40], edx             
0x005665be  mov     eax, dword ptr [ebp + 0x20]             
0x005665c1  mov     dword ptr [esi + 0x44], eax             
0x005665c4  mov     ecx, dword ptr [ebp + 0x24]             
0x005665c7  mov     dword ptr [esi + 0x48], ecx             
0x005665ca  mov     edx, dword ptr [ebp + 0x28]             
0x005665cd  mov     dword ptr [esi + 0x4c], edx             
0x005665d0  mov     eax, dword ptr [ebp + 0x2c]             
0x005665d3  mov     dword ptr [esi + 0x50], eax             
0x005665d6  mov     ecx, dword ptr [ebp + 0x30]             
0x005665d9  mov     dword ptr [esi + 0x54], ecx             
0x005665dc  mov     edx, dword ptr [ebp + 0x34]             
0x005665df  mov     dword ptr [esi + 0x58], edx             
0x005665e2  mov     eax, dword ptr [ebp + 0x38]             
0x005665e5  mov     dword ptr [esi + 0x5c], eax             
0x005665e8  mov     ecx, dword ptr [ebp + 0x3c]             
0x005665eb  mov     dword ptr [esi + 0x60], ecx             
0x005665ee  mov     edx, dword ptr [ebp + 0x40]             
0x005665f1  lea     eax, [ebp + 0x44]                       
0x005665f4  mov     dword ptr [esi + 0x64], edx             
0x005665f7  lea     ecx, [esi + 0x68]                       
0x005665fa  mov     edx, dword ptr [eax]                    
0x005665fc  mov     dword ptr [ecx], edx                    
0x005665fe  mov     edx, dword ptr [eax + 4]                
0x00566601  mov     dword ptr [ecx + 4], edx                
0x00566604  mov     edx, dword ptr [eax + 8]                
0x00566607  mov     dword ptr [ecx + 8], edx                
0x0056660a  mov     eax, dword ptr [eax + 0xc]              
0x0056660d  mov     dword ptr [ecx + 0xc], eax              
0x00566610  mov     ecx, dword ptr [ebp + 0x54]             
0x00566613  mov     dword ptr [esi + 0x78], ecx             
0x00566616  mov     ecx, edi                                
0x00566618  lea     eax, [esi + 0x7c]                       
0x0056661b  sub     ecx, esi                                
0x0056661d  mov     dx, word ptr [ecx + eax]                
0x00566621  mov     word ptr [eax], dx                      
0x00566624  mov     edx, dword ptr [esp + 0x48]             
0x00566628  add     eax, 2                                  
0x0056662b  dec     edx                                     
0x0056662c  mov     dword ptr [esp + 0x48], edx             
0x00566630  jne     0x56661d                                
0x00566632  lea     eax, [esi + 0xac]                       
0x00566638  mov     dword ptr [esp + 0x48], 0x18            
0x00566640  mov     dx, word ptr [eax + ecx]                
0x00566644  mov     word ptr [eax], dx                      
0x00566647  mov     edx, dword ptr [esp + 0x48]             
0x0056664b  add     eax, 2                                  
0x0056664e  dec     edx                                     
0x0056664f  mov     dword ptr [esp + 0x48], edx             
0x00566653  jne     0x566640                                
0x00566655  mov     eax, dword ptr [edi + 0xdc]             
0x0056665b  mov     dword ptr [esi + 0xdc], eax             
0x00566661  mov     ecx, dword ptr [edi + 0xe0]             
0x00566667  mov     dword ptr [esi + 0xe0], ecx             
0x0056666d  mov     edx, dword ptr [edi + 0xe4]             
0x00566673  mov     dword ptr [esi + 0xe4], edx             
0x00566679  mov     eax, dword ptr [edi + 0xe8]             
0x0056667f  mov     dword ptr [esi + 0xe8], eax             
0x00566685  mov     ecx, dword ptr [edi + 0xec]             
0x0056668b  mov     dword ptr [esi + 0xec], ecx             
0x00566691  mov     edx, dword ptr [edi + 0xf0]             
0x00566697  mov     dword ptr [esi + 0xf0], edx             
0x0056669d  mov     eax, dword ptr [edi + 0xf4]             
0x005666a3  mov     dword ptr [esi + 0xf4], eax             
0x005666a9  mov     ecx, dword ptr [edi + 0xf8]             
0x005666af  mov     dword ptr [esi + 0xf8], ecx             
0x005666b5  mov     edx, dword ptr [edi + 0xfc]             
0x005666bb  mov     dword ptr [esi + 0xfc], edx             
0x005666c1  mov     eax, dword ptr [edi + 0x100]            
0x005666c7  mov     dword ptr [esi + 0x100], eax            
0x005666cd  mov     ecx, dword ptr [edi + 0x104]            
0x005666d3  mov     dword ptr [esi + 0x104], ecx            
0x005666d9  mov     dx, word ptr [edi + 0x108]              
0x005666e0  mov     word ptr [esi + 0x108], dx              
0x005666e7  mov     ax, word ptr [edi + 0x10a]              
0x005666ee  mov     word ptr [esi + 0x10a], ax              
0x005666f5  mov     cx, word ptr [edi + 0x10c]              
0x005666fc  mov     word ptr [esi + 0x10c], cx              
0x00566703  mov     dx, word ptr [edi + 0x10e]              
0x0056670a  mov     word ptr [esi + 0x10e], dx              
0x00566711  mov     ax, word ptr [edi + 0x110]              
0x00566718  mov     word ptr [esi + 0x110], ax              
0x0056671f  mov     cx, word ptr [edi + 0x112]              
0x00566726  mov     word ptr [esi + 0x112], cx              
0x0056672d  mov     dx, word ptr [edi + 0x114]              
0x00566734  mov     word ptr [esi + 0x114], dx              
0x0056673b  mov     ax, word ptr [edi + 0x116]              
0x00566742  mov     word ptr [esi + 0x116], ax              
0x00566749  mov     cl, byte ptr [edi + 0x118]              
0x0056674f  mov     byte ptr [esi + 0x118], cl              
0x00566755  mov     dl, byte ptr [edi + 0x119]              
0x0056675b  mov     byte ptr [esi + 0x119], dl              
0x00566761  mov     al, byte ptr [edi + 0x11a]              
0x00566767  mov     byte ptr [esi + 0x11a], al              
0x0056676d  mov     cl, byte ptr [edi + 0x11b]              
0x00566773  mov     byte ptr [esi + 0x11b], cl              
0x00566779  mov     dl, byte ptr [edi + 0x11c]              
0x0056677f  mov     byte ptr [esi + 0x11c], dl              
0x00566785  mov     al, byte ptr [edi + 0x11d]              
0x0056678b  mov     byte ptr [esi + 0x11d], al              
0x00566791  mov     cl, byte ptr [edi + 0x11e]              
0x00566797  mov     byte ptr [esi + 0x11e], cl              
0x0056679d  mov     dl, byte ptr [edi + 0x11f]              
0x005667a3  mov     byte ptr [esi + 0x11f], dl              
0x005667a9  mov     al, byte ptr [edi + 0x120]              
0x005667af  mov     byte ptr [esi + 0x120], al              
0x005667b5  mov     cl, byte ptr [edi + 0x121]              
0x005667bb  mov     byte ptr [esi + 0x121], cl              
0x005667c1  mov     dl, byte ptr [edi + 0x122]              
0x005667c7  mov     byte ptr [esi + 0x122], dl              
0x005667cd  mov     eax, dword ptr [edi + 0x124]            
0x005667d3  mov     dword ptr [esi + 0x124], eax            
0x005667d9  mov     ecx, dword ptr [edi + 0x128]            
0x005667df  mov     dword ptr [esi + 0x128], ecx            
0x005667e5  mov     ecx, dword ptr [esi + 0x12c]            
0x005667eb  mov     edx, dword ptr [edi + 0x12c]            
0x005667f1  mov     eax, ecx                                
0x005667f3  xor     eax, edx                                
0x005667f5  and     eax, 1                                  
0x005667f8  xor     eax, ecx                                
0x005667fa  mov     dword ptr [esi + 0x12c], eax            
0x00566800  mov     edx, dword ptr [edi + 0x12c]            
0x00566806  mov     ecx, eax                                
0x00566808  xor     ecx, edx                                
0x0056680a  and     ecx, 2                                  
0x0056680d  xor     ecx, eax                                
0x0056680f  mov     dword ptr [esi + 0x12c], ecx            
0x00566815  mov     edx, dword ptr [edi + 0x12c]            
0x0056681b  mov     eax, ecx                                
0x0056681d  xor     eax, edx                                
0x0056681f  and     eax, 4                                  
0x00566822  xor     eax, ecx                                
0x00566824  mov     dword ptr [esi + 0x12c], eax            
0x0056682a  mov     edx, dword ptr [edi + 0x12c]            
0x00566830  mov     ecx, eax                                
0x00566832  xor     ecx, edx                                
0x00566834  and     ecx, 8                                  
0x00566837  xor     ecx, eax                                
0x00566839  mov     dword ptr [esi + 0x12c], ecx            
0x0056683f  mov     eax, dword ptr [edi + 0x12c]            
0x00566845  mov     edx, ecx                                
0x00566847  xor     edx, eax                                
0x00566849  and     edx, 0x10                               
0x0056684c  xor     edx, ecx                                
0x0056684e  mov     dword ptr [esi + 0x12c], edx            
0x00566854  mov     eax, dword ptr [edi + 0x130]            
0x0056685a  mov     dword ptr [esi + 0x130], eax            
0x00566860  mov     ecx, dword ptr [edi + 0x134]            
0x00566866  mov     dword ptr [esi + 0x134], ecx            
```

---

补充：`0x56686C` 起是镜像版本（`edi`=源 / `esi`=目标），逻辑完全相同，
只是编译器给两个方向各生成了一份代码。同标记下断，注意基址区别。