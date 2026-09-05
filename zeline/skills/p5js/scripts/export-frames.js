#!/usr/bin/env node
/**
 * Deterministic headless frame capture for p5.js sketches.
 *
 * Renders a sketch HTML in headless Chrome and writes one PNG per frame, so
 * `ffmpeg` can stitch an exact-length video. The hard requirement is 1:1 frame
 * correspondence: capturing a *running* animation gives you whatever the browser
 * happened to paint when the screenshot landed, and the result stutters at a
 * different framerate on every machine.
 *
 * The contract with the sketch, therefore:
 *
 *   1. the sketch calls `noLoop()` so nothing advances on its own;
 *   2. it sets `window._p5Ready = true` once `setup()` has finished (fonts,
 *      shaders and images loaded — not merely constructed);
 *   3. this script calls `redraw()` exactly once per captured frame.
 *
 * A sketch that keeps looping still captures, but the frame indices no longer
 * correspond to its internal time and the output will judder. The script warns
 * rather than failing, because a preview of a looping sketch is still useful.
 *
 * Requirements: `npm i -g puppeteer` (or `npx puppeteer`).
 *
 * Usage:
 *   node export-frames.js sketch.html --frames 300
 *   node export-frames.js sketch.html --width 3840 --height 2160 --frames 1
 *   node export-frames.js sketch.html --frames 120 --out frames --fps 60
 *
 * Then:
 *   ffmpeg -framerate 60 -i frames/frame-%05d.png -c:v libx264 -pix_fmt yuv420p out.mp4
 */
'use strict';

const fs = require('fs');
const path = require('path');

function parseArgs(argv) {
  const options = {
    file: null,
    width: 1080,
    height: 1080,
    frames: 1,
    fps: 60,
    out: 'frames',
    scale: 1,
    timeout: 30000,
  };
  const rest = [];
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith('--')) {
      rest.push(token);
      continue;
    }
    const key = token.slice(2);
    if (!(key in options)) {
      throw new Error(`unknown option --${key}`);
    }
    const value = argv[index + 1];
    if (value === undefined || value.startsWith('--')) {
      throw new Error(`--${key} needs a value`);
    }
    options[key] = key === 'out' ? value : Number(value);
    index += 1;
  }
  options.file = rest[0];
  if (!options.file) {
    throw new Error('give a sketch HTML file');
  }
  for (const key of ['width', 'height', 'frames', 'fps', 'scale']) {
    if (!Number.isFinite(options[key]) || options[key] <= 0) {
      throw new Error(`--${key} must be a positive number`);
    }
  }
  return options;
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(`error: ${error.message}`);
    console.error('usage: node export-frames.js sketch.html [--frames N] [--width W] [--height H] [--fps F] [--out DIR] [--scale S]');
    process.exit(2);
  }

  const sketch = path.resolve(options.file);
  if (!fs.existsSync(sketch)) {
    console.error(`error: no such file: ${sketch}`);
    process.exit(2);
  }
  fs.mkdirSync(options.out, { recursive: true });

  let puppeteer;
  try {
    puppeteer = require('puppeteer');
  } catch (error) {
    console.error('error: puppeteer is not installed. Run: npm i -g puppeteer');
    process.exit(3);
  }

  const browser = await puppeteer.launch({
    headless: 'new',
    // WebGL sketches render blank in headless Chrome without a GL backend.
    args: ['--no-sandbox', '--use-gl=swiftshader', '--enable-unsafe-swiftshader'],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({
      width: options.width,
      height: options.height,
      deviceScaleFactor: options.scale,
    });
    page.on('pageerror', (error) => console.error(`  [sketch error] ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') console.error(`  [console] ${message.text()}`);
    });

    await page.goto(`file://${sketch}`, { waitUntil: 'load', timeout: options.timeout });

    // Wait for the sketch to declare itself ready rather than guessing with a
    // sleep: fonts and shaders load asynchronously and a fixed delay either
    // wastes seconds per run or captures a half-built first frame.
    let ready = true;
    try {
      await page.waitForFunction('window._p5Ready === true', { timeout: options.timeout });
    } catch (error) {
      ready = false;
      console.error('  warning: window._p5Ready was never set — capturing anyway.');
      console.error('           Set it at the end of setup() for deterministic frames.');
    }

    const looping = await page.evaluate(
      () => typeof window.isLooping === 'function' ? window.isLooping() : null,
    );
    if (looping === true && options.frames > 1) {
      console.error('  warning: sketch is still looping; call noLoop() for exact frame timing.');
    }

    const canvas = await page.$('canvas');
    if (!canvas) {
      throw new Error('no <canvas> found — is this a p5.js sketch?');
    }

    const pad = String(options.frames).length + 1;
    for (let frame = 0; frame < options.frames; frame += 1) {
      const name = path.join(options.out, `frame-${String(frame).padStart(pad, '0')}.png`);
      await canvas.screenshot({ path: name, omitBackground: false });
      if (frame % 25 === 0 || frame === options.frames - 1) {
        process.stdout.write(`\r  captured ${frame + 1}/${options.frames}`);
      }
      if (frame < options.frames - 1) {
        // One redraw per frame: this is what keeps output frame N tied to the
        // sketch's own frame N.
        await page.evaluate(() => {
          if (typeof window.redraw === 'function') window.redraw();
        });
      }
    }
    process.stdout.write('\n');

    console.log(`  wrote ${options.frames} frame(s) to ${options.out}/`);
    console.log(`  stitch: ffmpeg -framerate ${options.fps} -i ${options.out}/frame-%0${pad}d.png -c:v libx264 -pix_fmt yuv420p out.mp4`);
    if (!ready) process.exitCode = 1;
  } catch (error) {
    console.error(`error: ${error.message}`);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main();
