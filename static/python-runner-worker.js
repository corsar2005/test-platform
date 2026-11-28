importScripts('vendor/skulpt.min.js', 'vendor/skulpt-stdlib.js');

const MAX_OUTPUT_LENGTH = 20_000;
const BLOCKED_MODULE_PATH = /^src\/lib\/(?:antigravity\.py|cgi(?:tb)?\.py|ftplib\.py|httplib\.py|imaplib\.py|poplib\.py|smtplib\.py|socket\.py|ssl\.py|telnetlib\.py|urllib(?:\/.*|2\.py)|webbrowser(?:\.js|\.py)|xmlrpc(?:\/.*|\.py))$/;

function readBuiltinModule(path) {
  if (BLOCKED_MODULE_PATH.test(path)) {
    throw new Error('Этот модуль недоступен в локальном компиляторе.');
  }
  const files = Sk.builtinFiles && Sk.builtinFiles.files;
  if (files && Object.prototype.hasOwnProperty.call(files, path)) {
    return files[path];
  }
  throw new Error(`Модуль недоступен: ${path}`);
}

self.addEventListener('message', async (event) => {
  let output = '';
  try {
    Sk.configure({
      __future__: Sk.python3,
      execLimit: 2500,
      yieldLimit: 100,
      output(text) {
        if (output.length + text.length > MAX_OUTPUT_LENGTH) {
          throw new Error('Вывод превышает лимит 20 000 символов.');
        }
        output += text;
      },
      read: readBuiltinModule,
    });
    await Sk.misceval.asyncToPromise(() => (
      Sk.importMainWithBody('<stdin>', false, event.data.code, true)
    ));
    self.postMessage({ output });
  } catch (error) {
    self.postMessage({ output, error: String(error) });
  }
});
