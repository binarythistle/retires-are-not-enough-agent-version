const form = document.querySelector('#chat-form');
const input = document.querySelector('#message');
const submitButton = form.querySelector('button[type="submit"]');
const chat = document.querySelector('.chat');
const toolbarStatus = document.querySelector('.chat-toolbar span:last-child');
const completeActions = document.querySelector('#journey-complete-actions');
const restartButton = document.querySelector('#restart-journey');
const conversationStorageKey = 'conference-booker-conversation-id';
let conversationId = sessionStorage.getItem(conversationStorageKey);

function addMessage(text, speaker, toolActivities = []) {
  const message = document.createElement('article');
  message.className = `message ${speaker}`;
  message.append(createMessageContent(text));

  if (speaker === 'agent' && toolActivities.length) {
    const activity = document.createElement('div');
    activity.className = 'tool-activity';
    const heading = document.createElement('span');
    heading.className = 'tool-activity-heading';
    heading.textContent = 'Agent activity';
    activity.append(heading);

    const calls = document.createElement('div');
    calls.className = 'tool-call-list';
    toolActivities.forEach((tool) => {
      const item = document.createElement('span');
      item.className = `tool-call ${tool.status}`;
      item.textContent = `${tool.name.replaceAll('_', ' ')} · ${tool.status} · ${tool.durationMilliseconds} ms`;
      calls.append(item);
    });
    activity.append(calls);

    message.append(activity);
  }

  chat.append(message);
  chat.scrollTop = chat.scrollHeight;
}

function addThinkingIndicator() {
  const indicator = document.createElement('article');
  indicator.className = 'message agent thinking';
  indicator.setAttribute('role', 'status');
  indicator.innerHTML = '<span class="thinking-orbit" aria-hidden="true"></span><span>Agent is planning the next step</span><span class="thinking-dots" aria-hidden="true"><i></i><i></i><i></i></span>';
  chat.append(indicator);
  chat.scrollTop = chat.scrollHeight;
  return indicator;
}

function createMessageContent(text) {
  const content = document.createElement('div');
  content.className = 'message-copy';
  let list;
  let currentItem;

  function flushList() {
    if (list) content.append(list);
    list = undefined;
    currentItem = undefined;
  }

  text.split('\n').forEach((line) => {
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);

    if (bullet) {
      list ??= document.createElement('ul');
      const item = document.createElement('li');
      item.className = 'conference-item';
      const title = document.createElement('span');
      title.className = 'conference-title';
      const titleAndDetails = bullet[1].match(/^\*\*(.+?)\*\*(?:\s*[—–-]\s*(.+))?$/);
      title.textContent = (titleAndDetails?.[1] ?? bullet[1]).replaceAll('**', '');
      item.append(title);
      if (titleAndDetails?.[2]) appendConferenceDetail(item, titleAndDetails[2]);
      list.append(item);
      currentItem = item;
      return;
    }

    if (list && currentItem && line.trim()) {
      appendConferenceDetail(currentItem, line.trim());
      return;
    }

    if (!line.trim()) {
      flushList();
      return;
    }

    flushList();

    if (line.trim()) {
      const paragraph = document.createElement('p');
      paragraph.textContent = line.replaceAll('**', '');
      content.append(paragraph);
    }
  });

  flushList();
  return content;
}

function appendConferenceDetail(item, text) {
  const detail = document.createElement('span');
  const cleanText = text.replaceAll('**', '').replace(/^_+|_+$/g, '');
  const hasMetadata = item.querySelector('.conference-meta');
  const looksLikeMetadata = /\b\d{4}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b|\||·/i.test(cleanText);

  detail.className = !hasMetadata && looksLikeMetadata
    ? 'conference-meta'
    : 'conference-description';
  detail.textContent = cleanText;
  item.append(detail);
}

function rememberConversation(id) {
  conversationId = id;
  sessionStorage.setItem(conversationStorageKey, id);
}

function setCompleteState(isComplete) {
  form.hidden = isComplete;
  completeActions.hidden = !isComplete;
  toolbarStatus.textContent = isComplete
    ? 'Workflow complete'
    : 'Model-directed tools enabled';
}

async function sendMessage(message) {
  const status = document.querySelector('#status');
  status?.remove();
  addMessage(message, 'user');
  const thinkingIndicator = addThinkingIndicator();
  input.disabled = true;
  submitButton.disabled = true;

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, conversationId })
    });
    const payload = await response.json();

    if (!response.ok) throw new Error(payload.error ?? 'The agent could not respond.');
    rememberConversation(payload.conversationId);
    addMessage(payload.message, 'agent', payload.toolActivities);
    setCompleteState(payload.isComplete);
  } catch (error) {
    addMessage(error.message, 'error');
  } finally {
    thinkingIndicator.remove();
    input.disabled = false;
    submitButton.disabled = false;
    input.focus();
  }
}

async function startConversation() {
  const status = document.querySelector('#status');

  try {
    if (conversationId) {
      const restored = await fetch(`/api/conversations/${conversationId}`);

      if (restored.ok) {
        const transcript = await restored.json();
        status?.remove();
        transcript.messages.forEach((entry) => addMessage(entry.message, entry.role, entry.toolActivities));
        setCompleteState(transcript.isComplete);
        return;
      }

      sessionStorage.removeItem(conversationStorageKey);
      conversationId = null;
    }

    const response = await fetch('/api/welcome', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversationId })
    });
    const payload = await response.json();

    if (!response.ok) throw new Error(payload.error ?? 'The agent could not respond.');
    rememberConversation(payload.conversationId);
    status?.remove();
    addMessage(payload.message, 'agent', payload.toolActivities);
    setCompleteState(payload.isComplete);
  } catch (error) {
    status.textContent = error.message;
    status.className = 'message error';

    // No conversation was opened, so there is nothing for a message to continue. Keeping the
    // box enabled would only produce a rejected turn; reloading is the way out.
    input.disabled = true;
    submitButton.disabled = true;
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;

  input.value = '';
  await sendMessage(message);
});

restartButton.addEventListener('click', async () => {
  const previous = conversationId;
  sessionStorage.removeItem(conversationStorageKey);

  if (previous) {
    // Close the old journey's workflow instead of leaving it to time out.
    try {
      await fetch(`/api/conversations/${previous}`, { method: 'DELETE' });
    } catch {
      // Starting fresh matters more than tidying up the old journey.
    }
  }

  window.location.reload();
});

startConversation();
