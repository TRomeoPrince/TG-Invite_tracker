const FOLDER_ID = '1wGYbuKYWSRR5L4aszAvfhxWe8BQkO-wX';

function doPost(e) {
  try {
    const expectedSecret =
      PropertiesService.getScriptProperties().getProperty('BACKUP_SECRET');

    if (!expectedSecret) {
      return jsonResponse({ ok: false, error: 'BACKUP_SECRET is not configured.' });
    }

    const body = JSON.parse(e.postData.contents || '{}');

    if (body.secret !== expectedSecret) {
      return jsonResponse({ ok: false, error: 'Unauthorized.' });
    }

    if (!body.filename || !body.data_base64) {
      return jsonResponse({ ok: false, error: 'Missing backup file data.' });
    }

    const bytes = Utilities.base64Decode(body.data_base64);
    const blob = Utilities.newBlob(
      bytes,
      body.mime_type || 'application/zip',
      body.filename
    );

    const folder = DriveApp.getFolderById(FOLDER_ID);
    const file = folder.createFile(blob);

    return jsonResponse({
      ok: true,
      file_id: file.getId(),
      file_name: file.getName(),
      file_url: file.getUrl()
    });
  } catch (err) {
    return jsonResponse({ ok: false, error: String(err) });
  }
}

function jsonResponse(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
