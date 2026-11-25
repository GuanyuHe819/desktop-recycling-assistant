const byId = id => document.getElementById(id);
const labelNames = {
  paper: 'Paper', plastic: 'Plastic', metal: 'Metal',
  cardboard: 'Cardboard', textile: 'Textile', empty: 'Empty scene',
};
const nameOf = label => labelNames[label] || label || '–';
const boardStates = {
  OFFLINE: ['Connecting', 'busy'], READY: ['Ready', 'ok'],
  STARTING: ['Starting', 'busy'], WAIT_ENTRY: ['Awaiting drop', 'ok'],
  FEEDBACK: ['Showing result', 'ok'], WAIT_CLEAR: ['Clear the bins', 'busy'],
  FAULT: ['Fault', 'error'], CONFIG_MISMATCH: ['Bin count mismatch', 'error'],
  UNKNOWN: ['Unknown', 'error'],
};
const cameraStates = {
  loading: ['Starting', 'busy', 'Loading the recognition model'],
  live: ['Live', 'ok', 'Opening the camera feed'],
  error: ['Unavailable', 'error', 'Could not open the camera or model'],
  stalled: ['Interrupted', 'error', 'Camera feed interrupted'],
};
const outcomes = {
  MATCHED: 'Matched', MISMATCH: 'Mismatched', TIMEOUT: 'Timed out',
  DISCONNECTED: 'Arduino offline', SENSOR_FAULT: 'Sensor fault',
  CLIENT_CONNECTION_LOST_UNKNOWN: 'MQTT disconnected',
  BOARD_CONNECTION_LOST_UNKNOWN: 'Arduino unresponsive', BOARD_RESTARTED_UNKNOWN: 'Arduino restarted',
  BOARD_FAULT_UNKNOWN: 'Arduino fault', ARM_ACK_UNKNOWN: 'No bin confirmation',
  ARM_REJECTED: 'Arduino declined recommendation', UNVERIFIED: 'Unconfirmed',
};

const feed = byId('camera-feed');
const actionButton = byId('round-action');
const guide = byId('guide');
const fullscreenButton = byId('fullscreen');
const modelSelect = byId('model-select');
const chartButtons = Array.from(document.querySelectorAll('[data-basis]'));
const rendered = {};
let state;
let basis = 'observed';
let actionBusy = false;
let offline = false;
let actionError = '';
let modelRequest = '';
let modelRequestError = '';
let feedRetryAt = 0;

function text(id, value) {
  const element = byId(id);
  if (element.textContent !== String(value)) element.textContent = value;
}

function device(id, message, tone) {
  text(id, message);
  byId(id).dataset.tone = tone;
}

function node(tag, value, className) {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = value;
  if (className) element.className = className;
  return element;
}

function changed(key, value) {
  const encoded = JSON.stringify(value);
  if (rendered[key] === encoded) return false;
  rendered[key] = encoded;
  return true;
}

function updateVideo(camera) {
  if (camera.phase === 'live') {
    if (!feed.hasAttribute('src') && Date.now() >= feedRetryAt) feed.src = '/camera.mjpg';
  } else {
    feed.removeAttribute('src');
    feed.hidden = true;
    byId('camera-placeholder').hidden = false;
  }
  text('camera-message', cameraStates[camera.phase][2]);
}

feed.addEventListener('load', () => {
  if (!feed.hasAttribute('src')) return;
  feed.hidden = false;
  byId('camera-placeholder').hidden = true;
});
feed.addEventListener('error', () => {
  feedRetryAt = Date.now() + 800;
  feed.removeAttribute('src');
  feed.hidden = true;
  byId('camera-placeholder').hidden = false;
});

function nextStep(data) {
  const phase = data.round.phase;
  const recent = data.round.recent[0];
  if (data.failure) return ['Restart the dashboard', 'Check the launcher window for details.', 'warning'];
  if (data.models?.pending || modelRequest) return ['Checking the model', 'You can start sorting after the selected model is ready.'];
  if (data.camera.phase !== 'live' && !data.pending) {
    return data.camera.phase === 'loading'
      ? ['Loading the camera and model', 'The camera feed will appear when it is ready.']
      : ['Check the camera', 'Close other apps using the camera, then restart the dashboard.', 'warning'];
  }
  if (!data.broker) {
    return [data.broker_lost ? 'MQTT connection lost' : 'Waiting for MQTT',
      'Check the hotspot and the launcher window.', 'warning'];
  }
  if (data.board === 'FAULT') {
    return ['Check the Arduino', 'Read the OLED or open Arduino Serial Monitor at 115200 baud.', 'warning'];
  }
  if (data.board === 'CONFIG_MISMATCH') {
    return ['Check the bin count', 'Compare the number of bins in the computer config and the Arduino sketch.', 'warning'];
  }
  if (['OFFLINE', 'UNKNOWN', 'STARTING', undefined].includes(data.board)) {
    const lost = data.board_seen && data.board === 'OFFLINE';
    return [lost ? 'Arduino is not responding' : 'Waiting for Arduino',
      'Check board power, the hotspot and the computer IP in the sketch.', lost ? 'warning' : 'neutral'];
  }
  if (data.armed) {
    const port = data.bins.findIndex(item => item.label === data.recommendation) + 1;
    return [`Use Bin ${port} · ${nameOf(data.recommendation)}`,
      'Check the green light, then bring the item close to that sensor.', 'armed'];
  }
  if (data.pending) return [`Confirming the ${nameOf(data.recommendation).toLowerCase()} bin`, 'Wait for the green light.'];
  if (data.board !== 'READY') {
    if (data.recommendation && recent?.confirmed) {
      return [`${outcomes[recent.outcome]} · Bin ${recent.actual} triggered`,
        'Clear the camera and bins before the next item.', recent.outcome === 'MATCHED' ? 'match' : 'mismatch'];
    }
    const title = data.recommendation && recent ? (outcomes[recent.outcome] || 'Unconfirmed') : 'Clear the bins';
    return [title, 'Move hands and items away and wait for Arduino to show Ready.'];
  }
  if (phase === 'ended') return ['Session complete', 'Review the results. Starting a new session resets the totals.'];
  if (phase === 'idle') return ['Ready to sort', 'Choose a recognition model, then select Start sorting.'];
  if (data.wait_empty) return ['Clear the camera view', 'Move the item away until the model detects an empty scene.'];
  if (data.camera.label !== 'empty' && data.camera.confident === false) {
    return ['Reposition the item', 'Hold one item still and turn it so the camera can see the material clearly.', 'warning'];
  }
  return ['Show one item', 'Hold it still in front of the camera until the prediction is stable.'];
}

function renderPorts(bins, {recommended, triggered, tone} = {}) {
  if (!changed('ports', [bins.map(bin => bin.label), recommended, triggered, tone])) return;
  byId('port-list').replaceChildren(...bins.map((bin, index) => {
    const port = node('li', undefined, 'port');
    if (bin.label === recommended) {
      port.dataset.active = 'true';
      port.setAttribute('aria-current', 'true');
    }
    if (index + 1 === triggered) {
      port.dataset.result = tone;
      port.setAttribute('aria-label', `Bin ${triggered}, ${nameOf(bin.label)}, sensor triggered`);
    }
    port.append(node('span', index + 1, 'port-number'), node('span', nameOf(bin.label), 'port-name'));
    return port;
  }));
}

function renderChart(bins) {
  const entries = bins.map(bin => [bin.label, bin[basis]]);
  if (!changed('chart', [basis, entries])) return;
  const max = Math.max(1, ...entries.map(([, count]) => count));
  const chart = byId('distribution-bars');
  chart.setAttribute('aria-label', `Confirmed drops by ${basis === 'observed' ? 'triggered' : 'recommended'} bin`);
  chart.replaceChildren(...entries.map(([label, count], index) => {
    const row = node('li');
    const caption = node('span', undefined, 'bar-label');
    caption.append(node('span', String(index + 1)), node('strong', nameOf(label)));
    const track = node('div', undefined, 'bar-track');
    track.setAttribute('aria-hidden', 'true');
    const fill = node('span', undefined, 'bar-value');
    fill.style.width = `${100 * count / max}%`;
    track.append(fill);
    row.append(caption, track, node('span', count, 'bar-count'));
    return row;
  }));
}

function renderResults(recent, bins) {
  if (!changed('recent', recent)) return;
  byId('results-empty').hidden = recent.length > 0;
  byId('results-table').hidden = !recent.length;
  byId('results-body').replaceChildren(...recent.map(result => {
    const row = node('tr');
    const actual = result.actual ? `Bin ${result.actual} · ${nameOf(bins[result.actual - 1].label)}` : '–';
    const tone = result.confirmed ? (result.outcome === 'MATCHED' ? 'match' : 'mismatch') : 'unknown';
    row.append(node('td', result.time), node('td', nameOf(result.label)), node('td', actual),
      node('td', outcomes[result.outcome] || 'Unconfirmed', `result-${tone}`));
    return row;
  }));
}

function renderModels(data) {
  const models = data.models;
  byId('model-controls').hidden = !models;
  if (!models) return;
  if (changed('models', models.options)) {
    const prompt = new Option('Select a model', '');
    prompt.disabled = true;
    modelSelect.replaceChildren(prompt, ...models.options.map(model => new Option(model.name, model.id)));
  }
  modelSelect.value = modelRequest || models.pending || models.active || '';
  modelSelect.disabled = !data.can_choose_model || !!modelRequest || !!models.pending;
  let note = modelRequestError || models.error || '';
  if (modelRequest || models.pending) note = 'Loading and validating the selected model…';
  else if (!note && (data.round.phase === 'running' || data.round.phase === 'ending')) {
    note = 'End this session to switch models.';
  }
  text('model-note', note);
  byId('model-note').hidden = !note;
  byId('model-note').dataset.error = String(!!(modelRequestError || models.error));
}

function render(data) {
  byId('sample-badge').hidden = !data.simulated;
  const camera = data.camera;
  const [cameraStatus, cameraTone] = cameraStates[camera.phase];
  const [boardStatus, boardTone] = data.board === 'OFFLINE' && data.board_seen
    ? ['Unresponsive', 'error'] : (boardStates[data.board] || boardStates.OFFLINE);
  device('camera-state', cameraStatus, cameraTone);
  if (data.broker || data.broker_lost || data.board === 'UNKNOWN') {
    device('broker-state', data.broker ? 'Connected' : (data.broker_lost ? 'Disconnected' : 'Unknown'), data.broker ? 'ok' : 'error');
  } else {
    device('broker-state', 'Connecting', 'busy');
  }
  device('board-state', boardStatus, boardTone);
  updateVideo(camera);
  const live = camera.phase === 'live';
  text('prediction-label', live ? nameOf(camera.label) : '–');
  text('prediction-score', live ? `${(camera.score * 100).toFixed(1)}%` : '–');
  let recognition = 'Waiting for camera';
  if (live) recognition = camera.stable ? 'Stable prediction' : 'Checking stability';
  if (live && camera.confident === false) recognition = 'Low confidence';
  text('recognition-state', recognition);
  byId('confidence-bar').value = live ? camera.score : 0;

  const [title, detail, tone = 'neutral'] = nextStep(data);
  const showingResult = tone === 'match' || tone === 'mismatch';
  text('next-title', title);
  text('next-detail', detail);
  text('cue-label', showingResult ? 'Drop result' : 'Next step');
  byId('next-action').dataset.tone = tone;
  renderPorts(data.bins, {
    recommended: data.armed ? data.recommendation : null,
    triggered: showingResult ? data.round.recent[0].actual : null,
    tone,
  });
  const alertMessage = actionError || data.failure || data.launch_note || outcomes[data.notice] || '';
  text('alert', alertMessage);
  byId('alert').hidden = !alertMessage;

  const round = data.round;
  text('session-model', round.model ? `Model: ${round.model}` : '');
  byId('session-model').hidden = !round.model;
  byId('session-model').title = round.model || '';
  const roundStatus = {
    idle: 'Sort one item at a time with the camera and bin sensors.',
    running: `Session active · started at ${round.started}`,
    ending: 'Ending session after the current drop',
    ended: `Session complete · ${round.started} to ${round.ended}`,
  };
  text('session-state', roundStatus[round.phase]);
  actionButton.textContent = {running: 'End session', ending: 'Ending…', ended: 'New session'}[round.phase] || 'Start sorting';
  actionButton.disabled = actionBusy || !!modelRequest || !!data.models?.pending
    || (round.phase !== 'running' && !data.can_start);
  renderModels(data);
  ['total', 'matched', 'mismatch'].forEach(id => text(id, round[id]));
  const ratio = round.total ? 100 * round.matched / round.total : 0;
  byId('match-segment').style.width = `${ratio}%`;
  byId('mismatch-segment').style.width = `${round.total ? 100 - ratio : 0}%`;
  text('match-note', round.total ? `Bin match rate: ${ratio.toFixed(0)}% (${round.matched} of ${round.total})` : 'Match rate appears after the first confirmed drop.');
  renderChart(data.bins);
  renderResults(round.recent, data.bins);
}

function showOffline() {
  offline = true;
  ['broker-state', 'board-state', 'camera-state'].forEach(id => device(id, 'Unknown', 'error'));
  updateVideo({phase: 'stalled'});
  text('prediction-label', '–');
  text('prediction-score', '–');
  text('recognition-state', 'Waiting for camera');
  byId('confidence-bar').value = 0;
  if (state) renderPorts(state.bins);
  text('cue-label', 'Next step');
  byId('next-action').dataset.tone = 'warning';
  actionButton.disabled = true;
  modelSelect.disabled = true;
  text('session-state', state ? 'Disconnected · showing last synced results' : 'Trying to reconnect');
  text('next-title', 'Dashboard connection lost');
  text('next-detail', 'Check that the launcher window is still running.');
}

async function poll() {
  try {
    const result = await fetch('/api/status', {signal: AbortSignal.timeout(2300)});
    if (!result.ok) throw new Error('status unavailable');
    state = await result.json();
    offline = false;
    render(state);
  } catch {
    showOffline();
  } finally {
    setTimeout(poll, 300);
  }
}

actionButton.addEventListener('click', async () => {
  if (!state || offline || actionBusy) return;
  const action = state.round.phase === 'running' ? 'end' : 'start';
  actionBusy = true;
  actionError = '';
  if (action === 'start') modelRequestError = '';
  actionButton.disabled = true;
  try {
    const result = await fetch(`/api/round/${action}`, {
      method: 'POST', headers: {'X-Recycling-Dashboard': '1'}, signal: AbortSignal.timeout(4500),
    });
    if (!result.ok) actionError = (await result.json()).error;
  } catch {
    actionError = 'The request did not complete. Check the session status.';
  } finally {
    actionBusy = false;
  }
});

modelSelect.addEventListener('change', async () => {
  if (!state?.can_choose_model || offline || modelRequest || !modelSelect.value) return;
  modelRequest = modelSelect.value;
  modelRequestError = '';
  render(state);
  try {
    const result = await fetch('/api/models/select', {
      method: 'POST',
      headers: {'X-Recycling-Dashboard': '1', 'Content-Type': 'application/json'},
      body: JSON.stringify({id: modelRequest}),
      signal: AbortSignal.timeout(4500),
    });
    if (!result.ok) modelRequestError = (await result.json()).error;
  } catch {
    modelRequestError = 'Could not request the model. Check that the station is connected.';
  } finally {
    modelRequest = '';
  }
});

for (const button of chartButtons) {
  button.addEventListener('click', () => {
    basis = button.dataset.basis;
    for (const option of chartButtons) option.setAttribute('aria-pressed', option === button);
    if (state) renderChart(state.bins);
  });
}

byId('guide-open').addEventListener('click', () => guide.showModal());
if (document.fullscreenEnabled) {
  fullscreenButton.hidden = false;
  fullscreenButton.addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      actionError = 'Full screen could not open. Use your browser’s full-screen command.';
    }
  });
  document.addEventListener('fullscreenchange', () => {
    text('fullscreen', document.fullscreenElement ? 'Exit full screen' : 'Full screen');
  });
}
poll();
