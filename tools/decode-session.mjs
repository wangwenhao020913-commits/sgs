#!/usr/bin/env node
/**
 * 解出 DSH 会话日志的**全部**明文。
 *
 * DSH 把会话日志写成**拼接的 zstd 帧**：一个批次一帧，追加即追加一帧。
 * Node 的 `zstdDecompressSync` 一次性只解**第一帧**——直接拿它读日志会静默丢掉
 * 后面所有批次，只拿到头部。本工具先用 `scanZstdFrames` 扫出每帧的字节边界
 * （只读帧结构，不解压），再逐帧解压、按序拼接。
 *
 * 用法：
 *   node tools/decode-session.mjs --in <session.vN.jsonl.zstd> --out <decoded.jsonl>
 *
 * 日志位置：`~/.dsh/sessions/<cwd-slug>/<session-id>/session.vN.jsonl.zstd`。
 * 输出摘要 `frames=<帧数> lines=<行数> bytes=<字节数>`。
 * 退出码：0 全部帧解出 / 1 读取或解码失败 / 2 用法错误 / 3 末帧不完整（只解出完整帧）。
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { zstdDecompressSync } from 'node:zlib'

const ZSTD_MAGIC = 0xFD2FB528

/**
 * 只读 zstd 帧结构，扫出每帧的 `[start, end)` 字节区间（不解压任何块）。
 *
 * 完整帧结构非法即抛错；文件在末帧中途截断（正在写入）时不抛错，而是把该帧起点
 * 放进 `tornStart`，让调用方决定怎么处理已完整的那部分。
 *
 * @param {Buffer} buffer 日志当前的全部字节。
 * @returns {{ frames: {start: number, end: number}[], tornStart?: number }}
 */
export function scanZstdFrames(buffer) {
  const frames = []
  let offset = 0

  while (offset < buffer.length) {
    const start = offset
    if (buffer.length - offset < 4) return { frames, tornStart: start }
    if (buffer.readUInt32LE(offset) !== ZSTD_MAGIC) {
      throw new Error(`corrupt Zstandard session log: invalid frame magic at byte ${offset}`)
    }
    offset += 4

    // 帧头描述符：Content_Size_Flag(2b) | Single_Segment(1b) | 未使用(1b) | 保留(1b) | Checksum(1b) | Dict_ID(2b)
    if (offset === buffer.length) return { frames, tornStart: start }
    const descriptor = buffer.readUInt8(offset)
    offset += 1
    if ((descriptor & 0x18) !== 0) {
      throw new Error(`corrupt Zstandard session log: reserved frame-header bit at byte ${offset - 1}`)
    }

    const contentSizeFlag = descriptor >>> 6
    const singleSegment = (descriptor & 0x20) !== 0
    const checksum = (descriptor & 0x04) !== 0
    const dictionaryFlag = descriptor & 0x03
    const dictionaryBytes = dictionaryFlag === 3 ? 4 : dictionaryFlag
    const contentSizeBytes = contentSizeFlag === 0 ? (singleSegment ? 1 : 0) : 1 << contentSizeFlag
    const remainingHeaderBytes = (singleSegment ? 0 : 1) + dictionaryBytes + contentSizeBytes
    if (buffer.length - offset < remainingHeaderBytes) return { frames, tornStart: start }
    offset += remainingHeaderBytes

    for (;;) {
      if (buffer.length - offset < 3) return { frames, tornStart: start }
      const blockHeader = buffer.readUIntLE(offset, 3)
      offset += 3
      const lastBlock = (blockHeader & 1) !== 0
      const blockType = (blockHeader >>> 1) & 0x03
      const blockSize = blockHeader >>> 3
      if (blockType === 0x03) {
        throw new Error(`corrupt Zstandard session log: reserved block type at byte ${offset - 3}`)
      }
      const payloadBytes = blockType === 0x01 ? 1 : blockSize
      if (buffer.length - offset < payloadBytes) return { frames, tornStart: start }
      offset += payloadBytes
      if (lastBlock) break
    }

    if (checksum) {
      if (buffer.length - offset < 4) return { frames, tornStart: start }
      offset += 4
    }

    frames.push({ start, end: offset })
  }

  return { frames }
}

/** JSONL 行数 = 换行符个数；末行没有换行时也算一行。 */
function countLines(buffer) {
  if (buffer.length === 0) return 0
  let lines = 0
  for (const byte of buffer) {
    if (byte === 0x0a) lines += 1
  }
  if (buffer[buffer.length - 1] !== 0x0a) lines += 1
  return lines
}

/**
 * 解出一份拼接帧日志的全部明文。
 *
 * @param {Buffer} buffer 日志当前的全部字节。
 * @returns {{ frameCount: number, lineCount: number, tornStart?: number, plaintext: Buffer }}
 */
export function decodeSession(buffer) {
  const { frames, tornStart } = scanZstdFrames(buffer)
  const parts = frames.map((frame) => zstdDecompressSync(buffer.subarray(frame.start, frame.end)))
  const plaintext = Buffer.concat(parts)

  return { frameCount: frames.length, lineCount: countLines(plaintext), tornStart, plaintext }
}

const USAGE = '用法：node tools/decode-session.mjs --in <session.vN.jsonl.zstd> --out <decoded.jsonl>'

function parseArgs(argv) {
  const options = { input: undefined, output: undefined }

  for (let index = 0; index < argv.length; index += 1) {
    const separator = argv[index].indexOf('=')
    const flag = separator === -1 ? argv[index] : argv[index].slice(0, separator)
    const inlineValue = separator === -1 ? undefined : argv[index].slice(separator + 1)

    if (flag === '--in') {
      if (inlineValue === undefined) index += 1
      options.input = inlineValue ?? argv[index]
    } else if (flag === '--out') {
      if (inlineValue === undefined) index += 1
      options.output = inlineValue ?? argv[index]
    } else {
      throw new Error(`未知参数：${argv[index]}`)
    }
  }

  return options
}

/** 命令行入口；返回进程退出码（0 全部解出 / 1 读或解失败 / 2 用法错误 / 3 末帧不完整）。 */
function main(argv) {
  let options
  try {
    options = parseArgs(argv)
  } catch (error) {
    console.error(`decode-session: ${error.message}`)
    console.error(USAGE)
    return 2
  }

  if (!options.input || !options.output) {
    console.error('decode-session: 必须同时给出 --in 与 --out')
    console.error(USAGE)
    return 2
  }

  let buffer
  try {
    buffer = readFileSync(options.input)
  } catch (error) {
    console.error(`decode-session: 读不到 ${options.input}：${error.message}`)
    return 1
  }

  let decoded
  try {
    decoded = decodeSession(buffer)
  } catch (error) {
    console.error(`decode-session: ${error.message}`)
    return 1
  }

  if (decoded.frameCount === 0) {
    console.error('decode-session: 没扫到任何完整的 zstd 帧（空文件或只剩残帧）')
    return 1
  }

  writeFileSync(options.output, decoded.plaintext)
  console.log(
    `frames=${decoded.frameCount} lines=${decoded.lineCount} bytes=${decoded.plaintext.length} out=${options.output}`,
  )
  if (decoded.tornStart !== undefined) {
    // 退出码 3 = 部分解出：完整帧已写出，但残帧之后的事件不在输出里，别把它当完整日志用。
    console.error(`decode-session: 警告——末帧不完整（自字节 ${decoded.tornStart} 起），已跳过该残帧`)
    return 3
  }
  return 0
}

// 仅在被当作脚本直接运行时跑 CLI；被 import 时只暴露 scanZstdFrames / decodeSession。
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exit(main(process.argv.slice(2)))
}
