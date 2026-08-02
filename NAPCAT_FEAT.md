# [Feature] 支持发送不在内置 face_config.json 中的表情 ID

## 背景 / 问题
`send_msg` 发送 `face` 段时，如果表情 ID 不在 NapCat 内置的 `face_config.json` 里，该段会被**静默丢弃**，导致部分真实存在、客户端可正常显示的表情（如 id=469）无法通过 API 发送。

- 用户在 QQ 客户端里可以直接发出 id=469；
- `get_msg` 也能把 469 解析回来（`{"type":"face","data":{"id":469}}`）；
- 唯独 `send_msg` 发送被拦截，表现为"表情发不出来"。

## 复现
```
send_group_msg { group_id: xxx, message: [{ type: "face", data: { id: 469 } }] }
```
日志输出 `不支持的ID 469`，消息里没有该表情（被丢弃）。

## 代码位置
`packages/napcat-onebot/api/msg.ts`（约 684-715 行）的 face 段转换：

```ts
[OB11MessageDataType.face]: async ({ data: { id, resultId, chainCount } }) => {
  const parsedFaceId = +id;
  const sysFaces = faceConfig.sysface;
  const face = sysFaces.find((systemFace) => systemFace.QSid === parsedFaceId.toString());
  if (!face) {
    this.core.context.logger.logError('不支持的ID', id);
    return undefined;              // ← 直接丢弃
  }
  let faceType = 1;
  if (parsedFaceId >= 222) faceType = 2;
  if (face.AniStickerType) faceType = 3;
  return {
    elementType: ElementType.FACE,
    elementId: '',
    faceElement: {
      faceIndex: parsedFaceId,      // 用的是用户传入的原始 ID
      faceType,
      faceText: face.QDes,
      stickerId: face.AniStickerId,
      stickerType: face.AniStickerType,
      packId: face.AniStickerPackId,
      sourceType: 1,
      resultId: resultId?.toString(),
      chainCount,
    },
  };
},
```

`faceConfig` 来自 `napcat-core/external/face_config.json`（`sysface` 目前只到 QSid=432）。

## 期望行为
不在内置表里、但客户端真实存在的表情 ID 也能作为 `face` 段发送，由客户端决定是否渲染；无效 ID 不应破坏整条消息。

## 可选方案

### 方案 A：补齐 `face_config.json`
把新版 QQ 新增的表情（如 469 等）补充进 `packages/napcat-core/external/face_config.json` 的 `sysface` 数组。

- 优点：改动小、行为最贴合现状。
- 缺点：这是静态快照，QQ 客户端持续新增表情，表会一直滞后，需要反复维护。

### 方案 B：兜底构造（推荐）
查不到 `face` 时不再 `return undefined`，而是兜底构造元素：

```ts
const faceType = parsedFaceId >= 222 ? 2 : 1;
return {
  elementType: ElementType.FACE,
  elementId: '',
  faceElement: {
    faceIndex: parsedFaceId,
    faceType,
    faceText: '',
    sourceType: 1,
    resultId: resultId?.toString(),
    chainCount,
  },
};
```

理由：
- `faceIndex` 本来就是用户传入的原始 ID，`faceType` 也完全可由 ID 推导（`>=222 → 2`），查表只用于取 `faceText` 和动图贴纸字段；
- 兜底后，非动图表情直接透传，客户端有完整表情表负责渲染，无效 ID 最多显示空白，不会破坏发送；
- 动图表情若查不到则退回静态，属于可接受的降级。

## 补充
- `get_msg`（接收路径）不查这张表，所以接收/解析高 ID 表情没有问题，只有发送被卡。
- 若担心"有效 ID 范围"，可以同时保留一个"透传白名单/范围"或仅对 `parsedFaceId >= 0` 兜底。
