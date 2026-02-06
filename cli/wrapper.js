#!/usr/bin/env node
/**
 * Auto-install wrapper for claude-memory MCP server.
 * Ensures dependencies are installed and dist is built before starting.
 */

import { spawn } from 'child_process';
import { existsSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PROJECT_ROOT = join(__dirname, '..');

function runCommand(command, args, label) {
  return new Promise((resolve, reject) => {
    console.error(`${label}...`);

    const child = spawn(command, args, {
      cwd: PROJECT_ROOT,
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: false
    });

    child.stdout.on('data', (data) => process.stderr.write(data));
    child.stderr.on('data', (data) => process.stderr.write(data));

    child.on('exit', (code) => {
      if (code === 0) {
        console.error(`${label} complete.`);
        resolve();
      } else {
        console.error(`ERROR: ${label} failed with exit code ${code}`);
        reject(new Error(`${label} failed with exit code ${code}`));
      }
    });

    child.on('error', (err) => {
      console.error(`ERROR: ${label} failed: ${err.message}`);
      reject(err);
    });
  });
}

async function main() {
  try {
    // Check if node_modules exists, install if missing
    const nodeModulesPath = join(PROJECT_ROOT, 'node_modules');
    if (!existsSync(nodeModulesPath)) {
      await runCommand('npm', ['install', '--prefer-offline', '--no-audit', '--no-fund'], 'Installing dependencies');
    }

    // Check if dist/server.js exists, build if missing
    const serverPath = join(PROJECT_ROOT, 'dist', 'server.js');
    if (!existsSync(serverPath)) {
      await runCommand('npm', ['run', 'build'], 'Building server');
    }

    // Spawn the MCP server
    const child = spawn(process.execPath, [serverPath], {
      stdio: 'inherit',
      shell: false
    });

    // Forward signals to child process
    process.on('SIGTERM', () => child.kill('SIGTERM'));
    process.on('SIGINT', () => child.kill('SIGINT'));

    child.on('exit', (code, signal) => {
      if (signal) {
        process.kill(process.pid, signal);
      } else {
        process.exit(code || 0);
      }
    });

    child.on('error', (err) => {
      console.error(`ERROR: Failed to start MCP server: ${err.message}`);
      process.exit(1);
    });

  } catch (error) {
    console.error(`ERROR: ${error.message}`);
    process.exit(1);
  }
}

main().catch((error) => {
  console.error(`Unexpected error: ${error.message}`);
  process.exit(1);
});
