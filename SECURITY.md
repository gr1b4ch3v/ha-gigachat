# Безопасность

## Как сообщить об уязвимости

Не открывайте публичный issue. Сообщите приватно через
[GitHub Security Advisories](https://github.com/gr1b4ch3v/ha-gigachat/security/advisories/new).
Исправления выходят для последней версии.

## Ключ авторизации

Не публикуйте ключ GigaChat в issue, логах и скриншотах. Если ключ попал в открытый доступ, перевыпустите
его в GigaChat Studio: старый перестанет работать, а Home Assistant предложит ввести новый. В диагностике
интеграции ключ и идентификатор клиента скрыты.

## Встроенный сертификат

Серверы GigaChat используют сертификаты национального удостоверяющего центра Минцифры. Поэтому
в интеграцию встроен его публичный корневой сертификат
`custom_components/gigachat/certs/russian_trusted_root_ca.pem`:

- субъект: `C=RU, O=The Ministry of Digital Development and Communications, CN=Russian Trusted Root CA`;
- источник: [портал Госуслуг](https://www.gosuslugi.ru/crt);
- SHA-256: `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`;
- действителен до 27.02.2032.

Сертификат добавляется только в TLS-контекст соединений интеграции и не меняет системное хранилище
доверенных сертификатов Home Assistant. Проверить отпечаток:

```bash
openssl x509 -in custom_components/gigachat/certs/russian_trusted_root_ca.pem -noout -fingerprint -sha256
```

---

# Security

## Reporting a vulnerability

Do not open a public issue. Report it privately through
[GitHub Security Advisories](https://github.com/gr1b4ch3v/ha-gigachat/security/advisories/new).
Fixes are released for the latest version.

## Authorization key

Do not post your GigaChat key in issues, logs or screenshots. If the key has leaked, regenerate it in
GigaChat Studio: the old key stops working and Home Assistant asks for the new one. The integration
diagnostics redact the key and the client ID.

## Bundled certificate

GigaChat servers use certificates of the national certificate authority of the Russian Ministry of Digital
Development, so the integration ships its public root certificate
`custom_components/gigachat/certs/russian_trusted_root_ca.pem`:

- subject: `C=RU, O=The Ministry of Digital Development and Communications, CN=Russian Trusted Root CA`;
- source: [Gosuslugi portal](https://www.gosuslugi.ru/crt);
- SHA-256: `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`;
- valid until 2032-02-27.

The certificate is only added to the TLS context of the integration's connections and does not change the
system trust store of Home Assistant. To check the fingerprint:

```bash
openssl x509 -in custom_components/gigachat/certs/russian_trusted_root_ca.pem -noout -fingerprint -sha256
```
