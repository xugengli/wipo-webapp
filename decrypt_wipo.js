const fs = require('fs');
const path = require('path');

// Try bundled crypto-js first (no npm install needed on Streamlit Cloud)
const CRYPTO_JS_PATHS = [
    path.join(__dirname, 'crypto-js.min.js'),
    path.join(__dirname, 'node_modules', 'crypto-js', 'crypto-js.js'),
];
let CryptoJS = null;
for (const p of CRYPTO_JS_PATHS) {
    if (fs.existsSync(p)) {
        CryptoJS = require(p);
        break;
    }
}
if (!CryptoJS) {
    CryptoJS = require('crypto-js');
}

const cipher = fs.readFileSync(process.argv[2] || 'wipo_cipher.txt', 'utf8').trim();
const hashSearch = fs.readFileSync(process.argv[3] || 'wipo_hash_search.txt', 'utf8').trim();

const key = CryptoJS.enc.Utf8.parse("8?)i_~Nk6qv0IX;2" + hashSearch);
const plaintext = CryptoJS.AES.decrypt(cipher, key, { mode: CryptoJS.mode.ECB }).toString(CryptoJS.enc.Utf8);

if (process.argv[4]) {
    fs.writeFileSync(process.argv[4], plaintext);
}
console.log(plaintext);
