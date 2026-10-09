// The header clocks: UTC, which the test source burns into its picture, and UTC−3 (Brasília, no daylight saving).
// Each tick is scheduled for the next whole second, so the page and the burned-in clock change together.
const ZONES = [['UTC', 0], ['UTC−3', -3]];

const two = (n) => String(n).padStart(2, '0');

export function clockText(ms, offsetHours) {
  const t = new Date(ms + offsetHours * 3600000);
  return two(t.getUTCHours()) + ':' + two(t.getUTCMinutes()) + ':' + two(t.getUTCSeconds());
}

export function startClocks(root, now = () => Date.now()) {
  const times = ZONES.map(([label]) => {
    const time = document.createElement('time');
    const zone = document.createElement('span');
    zone.className = 'zone';
    zone.textContent = label;
    const item = document.createElement('span');
    item.className = 'clock';
    item.append(zone, ' ', time);
    root.append(item);
    return time;
  });
  const tick = () => {
    const ms = now();
    ZONES.forEach(([, offset], i) => { times[i].textContent = clockText(ms, offset); });
    setTimeout(tick, 1000 - (ms % 1000) + 5);
  };
  tick();
}
