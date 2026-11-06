import { execFileSync } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, readFile, rename, rm } from 'node:fs/promises';
import { join, relative, resolve } from 'node:path';
import { build } from 'esbuild';

const staticFiles = ['manifest.json', 'options.html', 'options.css', 'offscreen.html'];
const packageZip = process.argv.includes('--zip');
await mkdir('dist', { recursive: true });
const result = await build({
  entryPoints: ['worker', 'offscreen', 'worklet', 'content', 'options'].map(name => 'src/' + name + '.ts'),
  outdir: 'dist',
  bundle: true,
  format: 'iife',
  target: 'chrome116',
  metafile: packageZip,
});
await Promise.all(staticFiles.map(name => copyFile(name, join('dist', name))));

if (packageZip) {
  const { version } = JSON.parse(await readFile('manifest.json', 'utf8'));
  const artifacts = resolve('../artifacts');
  await mkdir(artifacts, { recursive: true });
  const temporary = await mkdtemp(join(artifacts, '.package-'));
  const name = `live-video-tanslate-${version}-chrome.zip`;
  try {
    const archive = join(temporary, name);
    const bundles = Object.keys(result.metafile.outputs).map(file => relative('dist', file));
    execFileSync('zip', ['-q', '-X', archive, ...staticFiles, ...bundles], { cwd: 'dist' });
    const target = join(artifacts, name);
    await rename(archive, target);
    process.stdout.write(target + '\n');
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}
