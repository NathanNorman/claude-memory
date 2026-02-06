/**
 * Integration test for claude-memory MCP server.
 *
 * Spawns node dist/server.js as a child process with HOME set to a temp dir,
 * sends MCP JSON-RPC messages over stdin (Content-Length framed), reads
 * responses from stdout, and verifies all 3 tools work end-to-end.
 */

import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { strict as assert } from 'node:assert';

// --- Temp HOME ---
const tmpHome = mkdtempSync(join(tmpdir(), 'claude-memory-test-'));
// Pre-create the .claude-memory directory structure so the server can init DB
mkdirSync(join(tmpHome, '.claude-memory', 'index'), { recursive: true });
mkdirSync(join(tmpHome, '.claude-memory', 'memory'), { recursive: true });

console.log(`[test] Using temp HOME: ${tmpHome}`);

// --- MCP JSON-RPC helpers ---

let msgId = 0;

function makeRequest(method, params = {}) {
  msgId++;
  return { jsonrpc: '2.0', id: msgId, method, params };
}

function encodeMessage(obj) {
  const body = JSON.stringify(obj);
  const header = `Content-Length: ${Buffer.byteLength(body)}\r\n\r\n`;
  return header + body;
}

/**
 * Parse one or more JSON-RPC responses from a raw buffer.
 * The MCP protocol uses Content-Length framing.
 */
function parseResponses(raw) {
  const responses = [];
  let remaining = raw;

  while (remaining.length > 0) {
    const headerEnd = remaining.indexOf('\r\n\r\n');
    if (headerEnd === -1) break;

    const header = remaining.slice(0, headerEnd);
    const match = header.match(/Content-Length:\s*(\d+)/i);
    if (!match) break;

    const contentLength = parseInt(match[1], 10);
    const bodyStart = headerEnd + 4;
    const bodyEnd = bodyStart + contentLength;

    if (remaining.length < bodyEnd) break; // incomplete

    const body = remaining.slice(bodyStart, bodyEnd);
    try {
      responses.push(JSON.parse(body));
    } catch {
      // skip malformed
    }
    remaining = remaining.slice(bodyEnd);
  }

  return responses;
}

// --- Spawn MCP server ---

const serverPath = join(import.meta.dirname, '..', 'dist', 'server.js');

const child = spawn('node', [serverPath], {
  env: { ...process.env, HOME: tmpHome, USERPROFILE: tmpHome },
  stdio: ['pipe', 'pipe', 'pipe'],
});

let stdoutBuf = '';
let stderrBuf = '';

child.stdout.on('data', (chunk) => { stdoutBuf += chunk.toString(); });
child.stderr.on('data', (chunk) => { stderrBuf += chunk.toString(); });

function send(obj) {
  child.stdin.write(encodeMessage(obj));
}

/**
 * Wait for a JSON-RPC response with the given id.
 * Polls stdoutBuf with a timeout.
 */
async function waitForResponse(expectedId, timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const responses = parseResponses(stdoutBuf);
    const match = responses.find((r) => r.id === expectedId);
    if (match) {
      return match;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(
    `Timeout waiting for response id=${expectedId} after ${timeoutMs}ms.\n` +
    `stdout so far: ${stdoutBuf.slice(0, 500)}\n` +
    `stderr so far: ${stderrBuf.slice(0, 500)}`
  );
}

// --- Test Flow ---

async function runTests() {
  // 1. Send initialize
  console.log('[test] Sending initialize...');
  const initReq = makeRequest('initialize', {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'integration-test', version: '1.0.0' },
  });
  send(initReq);
  const initResp = await waitForResponse(initReq.id);
  assert.ok(initResp.result, 'initialize should return result');
  assert.ok(initResp.result.serverInfo, 'initialize should return serverInfo');
  console.log(`[test] Server initialized: ${initResp.result.serverInfo.name}`);

  // Send initialized notification (no id, no response expected)
  const initializedNotif = { jsonrpc: '2.0', method: 'notifications/initialized' };
  send(initializedNotif);
  // Brief pause for server to process notification
  await new Promise((resolve) => setTimeout(resolve, 200));

  // 2. Test memory_write — write known content
  console.log('[test] Testing memory_write...');
  const writeContent = '# Test Memory\n\nThis is a test memory about quantum computing and neural networks.\n\nQuantum entanglement enables faster-than-classical communication protocols.';
  const writeReq = makeRequest('tools/call', {
    name: 'memory_write',
    arguments: {
      content: writeContent,
      file: 'memory/test-integration.md',
      append: false,
    },
  });
  send(writeReq);
  const writeResp = await waitForResponse(writeReq.id);
  assert.ok(writeResp.result, 'memory_write should return result');
  assert.ok(!writeResp.result.isError, `memory_write should not error: ${JSON.stringify(writeResp.result)}`);
  const writeResult = JSON.parse(writeResp.result.content[0].text);
  assert.equal(writeResult.path, 'memory/test-integration.md', 'write path should match');
  assert.ok(writeResult.linesWritten > 0, 'should have written lines');
  console.log(`[test] memory_write OK: wrote ${writeResult.linesWritten} lines to ${writeResult.path}`);

  // 3. Test memory_search — search for the content we just wrote
  console.log('[test] Testing memory_search...');
  const searchReq = makeRequest('tools/call', {
    name: 'memory_search',
    arguments: {
      query: 'quantum computing neural networks',
      maxResults: 5,
      minScore: 0.01,
    },
  });
  send(searchReq);
  const searchResp = await waitForResponse(searchReq.id);
  assert.ok(searchResp.result, 'memory_search should return result');
  assert.ok(!searchResp.result.isError, `memory_search should not error: ${JSON.stringify(searchResp.result)}`);
  const searchResult = JSON.parse(searchResp.result.content[0].text);
  assert.ok(searchResult.results.length > 0, 'search should find at least 1 result');
  assert.ok(
    searchResult.results[0].snippet.includes('quantum') || searchResult.results[0].snippet.includes('neural'),
    'search result snippet should contain search terms'
  );
  assert.ok(searchResult.indexed.files > 0, 'should have indexed files');
  assert.ok(searchResult.indexed.chunks > 0, 'should have indexed chunks');
  console.log(`[test] memory_search OK: found ${searchResult.results.length} results (${searchResult.indexed.files} files, ${searchResult.indexed.chunks} chunks)`);

  // 4. Test memory_read — read back the file we wrote
  console.log('[test] Testing memory_read...');
  const readReq = makeRequest('tools/call', {
    name: 'memory_read',
    arguments: {
      path: 'memory/test-integration.md',
      from: 1,
      lines: 0,
    },
  });
  send(readReq);
  const readResp = await waitForResponse(readReq.id);
  assert.ok(readResp.result, 'memory_read should return result');
  assert.ok(!readResp.result.isError, `memory_read should not error: ${JSON.stringify(readResp.result)}`);
  const readResult = JSON.parse(readResp.result.content[0].text);
  assert.equal(readResult.text, writeContent, 'read content should match written content');
  assert.equal(readResult.path, 'memory/test-integration.md', 'read path should match');
  assert.ok(readResult.totalLines > 0, 'should have total lines');
  console.log(`[test] memory_read OK: ${readResult.totalLines} lines, content matches`);

  console.log('[test] All integration tests passed!');
}

// --- Run ---

try {
  await runTests();
  child.kill('SIGTERM');
  process.exit(0);
} catch (err) {
  console.error('[test] FAILED:', err.message);
  console.error('[test] stderr:', stderrBuf.slice(0, 1000));
  child.kill('SIGTERM');
  process.exit(1);
}
