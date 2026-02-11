#!/usr/bin/env node
import { spawn } from 'child_process';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const child = spawn(process.execPath, [join(__dirname, '..', 'dist', 'doctor-cli.js'), ...process.argv.slice(2)], { stdio: 'inherit' });
child.on('exit', (code) => process.exit(code || 0));
