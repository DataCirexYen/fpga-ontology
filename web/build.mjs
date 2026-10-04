import * as esbuild from 'esbuild';

const options = {
  entryPoints: ['src/app.jsx'],
  bundle: true,
  outfile: 'static/app.js',
  minify: true,
  target: ['es2020'],
  define: { 'process.env.NODE_ENV': '"production"' },
  legalComments: 'linked',
  loader: { '.woff': 'file', '.woff2': 'file', '.ttf': 'file', '.eot': 'file', '.svg': 'dataurl' },
  logLevel: 'info',
};

if (process.argv.includes('--watch')) {
  const context = await esbuild.context(options);
  await context.watch();
  console.log('Watching src/. Refresh the browser after changes.');
} else {
  await esbuild.build(options);
}
