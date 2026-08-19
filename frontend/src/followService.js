const STORAGE_KEY = "chainalpha-event-follows";

export function listFollows() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch (_) {
    return [];
  }
}

export function isFollowed(type, value) {
  return listFollows().some((item) => item.type === type && item.value === value);
}

export function toggleFollow(type, value) {
  const current = listFollows();
  const index = current.findIndex((item) => item.type === type && item.value === value);
  if (index >= 0) current.splice(index, 1);
  else current.push({id: `${type}:${value}`, type, value, createdAt: new Date().toISOString()});
  localStorage.setItem(STORAGE_KEY, JSON.stringify(current));
  return index < 0;
}
