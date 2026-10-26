# Third-party files in this folder

- `vis.bundle.js`: [Observable Plot](https://github.com/observablehq/plot) 0.6.16 (ISC licence, © Observable, Inc.), bundled with its dependency [D3](https://github.com/d3/d3) (ISC licence, © Mike Bostock) and [topojson-client](https://github.com/topojson/topojson-client) (ISC licence). Licence texts are appended at the end of the bundle. Rebuild with `tools/vendor` (`npm install && npm run build`).
- `countries-110m.json`: [world-atlas](https://github.com/topojson/world-atlas) 2.0.2 (ISC licence), derived from [Natural Earth](https://www.naturalearthdata.com/) data (public domain).
- `vis.bundle.js` also exports `geoOrthographic`, `geoPath`, `geoGraticule10` and `geoInterpolate` from [d3-geo](https://github.com/d3/d3-geo) (ISC licence) for the hero globe.
- `../fonts/`: [D-DIN](https://www.datto.com/fonts/d-din) by Datto Inc., SIL Open Font License 1.1 (`fonts/OFL-1.1.txt`, `fonts/FONTLOG.txt`), unmodified woff2 files.
