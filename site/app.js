'use strict';
// Local examples only: this static site does not fetch or scrape visitor URLs.
const samples = {
  products: {selector: '.product', records: [{name: 'Everyday notebook', price: '$12.00'}, {name: 'Studio pencil', price: null}, {name: 'Reading lamp', price: '$48.00'}], details: ['Paper goods / 01', 'Price available on request / 02', 'For your favorite corner / 03'], icons: ['▤', '✎', '◒']},
  articles: {selector: 'article', records: [{title: 'A slower kind of Sunday', category: 'Living'}, {title: 'Objects with a story', category: 'Design'}, {title: 'Notes from the garden', category: 'Outdoors'}], details: ['5 min read', '3 min read', '7 min read'], icons: ['01', '02', '03']},
  table: {selector: 'tbody tr', records: [{city: 'Cairo', temperature: '28 °C'}, {city: 'Lisbon', temperature: '21 °C'}, {city: 'Copenhagen', temperature: '14 °C'}]}
};
const examples = {
  python: {code: 'from pageharvest import Field, scrape, export_csv\n\npage = scrape(\n    "https://example.com/products",\n    items=".product",\n    fields={\n        "name": Field("h2", required=True),\n        "price": Field(".price"),\n    },\n)\n\nexport_csv(page.items, "products.csv")\nprint(page.ok, page.item_errors)', caption: 'Replace the example URL and selectors with your target page.'},
  cli: {code: '# Install the toolkit\npip install pageharvest\n\n# Turn a page into JSON\npageharvest https://example.com -o page.json\n\n# Run your saved recipe\npageharvest --recipe products.recipe.json', caption: 'A terminal command now. A repeatable workflow whenever you need it.'},
  recipe: {code: JSON.stringify({version: 1, urls: ['https://example.com/products'], items: '.product', fields: {name: {selector: 'h2', required: true}, price: '.price'}, output: {path: 'products.csv', format: 'csv'}}, null, 2), caption: 'Save as products.recipe.json. Customize the URL and CSS selectors.'}
};
let sample = 'products', format = 'json', code = 'python';
const output = document.querySelector('#output');
function selectButton(attribute, value) {
  document.querySelectorAll(`[data-${attribute}]`).forEach(button => button.setAttribute('aria-pressed', String(button.dataset[attribute] === value)));
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function renderSample() {
  const data = samples[sample], container = document.querySelector('#sample-content');
  container.replaceChildren();
  document.querySelector('#selector').textContent = data.selector;
  if (sample === 'table') {
    const table = element('table', undefined, 'sample-table');
    const head = element('thead'), row = element('tr');
    ['City', 'Temperature'].forEach(label => {const th = element('th', label); th.scope = 'col'; row.append(th);});
    head.append(row); table.append(head);
    const body = element('tbody');
    data.records.forEach(record => {const tr = element('tr'); Object.values(record).forEach(value => tr.append(element('td', value))); body.append(tr);});
    table.append(body); container.append(table);
  } else {
    data.records.forEach((record, index) => {
      const row = element('div', undefined, 'product-row');
      const icon = element('span', data.icons[index], 'product-icon'); icon.setAttribute('aria-hidden', 'true');
      const info = element('div'); info.append(element('strong', record.name || record.title), element('small', data.details[index]));
      row.append(icon, info, element('span', sample === 'products' ? record.price || '—' : record.category, 'product-price'));
      container.append(row);
    });
  }
  renderOutput();
}
function csvCell(value) {
  const text = value == null ? '' : String(value);
  return /[",\r\n]/.test(text) ? '"' + text.replaceAll('"', '""') + '"' : text;
}
function renderOutput() {
  const records = samples[sample].records, keys = Object.keys(records[0]);
  output.textContent = format === 'json' ? JSON.stringify(records, null, 2) : [keys, ...records.map(record => keys.map(key => record[key]))].map(row => row.map(csvCell).join(',')).join('\n');
  document.querySelector('#record-count').textContent = `${records.length} ${sample === 'table' ? 'table' : sample} records · ${format.toUpperCase()} ready`;
  output.classList.remove('changed'); void output.offsetWidth; output.classList.add('changed');
}
function renderCode() {
  document.querySelector('#code-example').textContent = examples[code].code;
  document.querySelector('#code-caption').textContent = examples[code].caption;
}
async function copy(text, button) {
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = 'Copied!';
    document.querySelector('#announcement').textContent = 'Copied to clipboard.';
  } catch {
    button.textContent = 'Select to copy';
    document.querySelector('#announcement').textContent = 'Clipboard unavailable. Select the displayed text and copy it manually.';
  }
  setTimeout(() => {button.textContent = original;}, 2000);
}
document.querySelectorAll('[data-sample]').forEach(button => button.addEventListener('click', () => {sample = button.dataset.sample; selectButton('sample', sample); renderSample();}));
document.querySelectorAll('[data-format]').forEach(button => button.addEventListener('click', () => {format = button.dataset.format; selectButton('format', format); renderOutput();}));
document.querySelectorAll('[data-code]').forEach(button => button.addEventListener('click', () => {code = button.dataset.code; selectButton('code', code); renderCode();}));
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', () => copy(button.dataset.copy, button)));
document.querySelector('#copy-output').addEventListener('click', event => copy(output.textContent, event.currentTarget));
document.querySelector('#copy-code').addEventListener('click', event => copy(examples[code].code, event.currentTarget));
renderSample(); renderCode();
