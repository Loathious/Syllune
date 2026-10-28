// Repository-scoped release checks. This module never executes downloaded code.
// The user opens the release page and installs a matching full installer.
// Missing release configuration is explicit rather than falling back upstream.
'use strict';
const https = require('https');
const brand = require('./brand.json');

function releaseURL() {
  return /^[\w.-]+\/[\w.-]+$/.test(brand.repository || '') ? `https://github.com/${brand.repository}/releases` : '';
}

function newer(latest, current) {
  const parse = value => /^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(value || '');
  const a = parse(latest), b = parse(current);
  if (!a || !b) return false;
  for (let i = 1; i <= 3; i++) {
    if (+a[i] !== +b[i]) return +a[i] > +b[i];
  }
  return false;
}

async function check() {
  const url = releaseURL();
  if (!url) return {configured: false, current: brand.version, message: 'This development copy has no public release repository yet.'};
  return new Promise((resolve, reject) => {
    const request = https.get(`https://api.github.com/repos/${brand.repository}/releases/latest`, {
      headers: {'User-Agent': `Syllune/${brand.version}`, Accept: 'application/vnd.github+json'}, timeout: 8000,
    }, response => {
      if (response.statusCode === 404) { response.resume(); resolve({configured: true, current: brand.version, url, message: 'No release has been published yet.'}); return; }
      if (response.statusCode !== 200) { response.resume(); reject(new Error(`Update check failed (${response.statusCode}). Try again later.`)); return; }
      let body = '';
      response.on('data', chunk => { body += chunk; if (body.length > 1048576) request.destroy(new Error('Release response too large')); });
      response.on('end', () => {
        try {
          const release = JSON.parse(body);
          resolve({configured: true, current: brand.version, latest: release.tag_name, available: newer(release.tag_name, brand.version), url});
        } catch { reject(new Error('The release service returned an unreadable response.')); }
      });
      response.on('error', reject);
    });
    request.on('timeout', () => request.destroy(new Error('Update check timed out. Check your connection and try again.')));
    request.on('error', reject);
  });
}

module.exports = {releaseURL, newer, check};
