const STORAGE_KEY = 'trippal-readiness-v1';
const SUBMISSION_KEY = 'trippal-skill-submission-v1';
const ASSESSMENT_KEY = 'trippal-skill-assessment-v1';
const form = document.querySelector('#trip-form');
const formSteps = [...document.querySelectorAll('.form-step')];
const stepItems = [...document.querySelectorAll('.step-list li')];
const nextButton = document.querySelector('#next-button');
const backButton = document.querySelector('#back-button');
const errorBox = document.querySelector('#form-error');
let currentStep = 0;
const LANGUAGE_KEY = 'trippal-language';
let currentLanguage = localStorage.getItem(LANGUAGE_KEY) || 'en';
const originalText = new WeakMap();

const TEXT_ZH = {
  'China readiness check': '中国行前准备检查', 'Draft saved on this device': '草稿已保存在此设备',
  'Your departure file': '你的出发档案', 'Trip basics': '行程基础', 'Route and timing': '路线与时间',
  'Connection': '网络连接', 'Phone and access': '手机与网络', 'Money': '支付准备', 'Wallets and backup': '钱包与备用方案',
  'Bookings': '预订事项', 'Stay and tickets': '住宿与票务', 'Fix first': '优先处理',
  'TripPal puts difficult-to-recover gaps before ordinary travel tips.': 'TripPal 会优先提示落地后难以补救的问题。',
  'Step 1 of 4': '第 1 步，共 4 步', 'Let’s map your route.': '先确认你的旅行路线。', 'About 4 min': '约 4 分钟',
  'Your route shapes entry checks, booking deadlines and what must happen before departure.': '你的路线会影响入境核验、预订截止时间，以及必须在出发前完成的准备。',
  'Passport nationality': '护照国籍', 'Choose one': '请选择', 'United States': '美国', 'Canada': '加拿大', 'United Kingdom': '英国', 'Australia': '澳大利亚', 'Singapore': '新加坡', 'Japan': '日本', 'Other': '其他',
  'Arriving in': '抵达城市', 'Choose a city': '请选择城市', 'Beijing': '北京', 'Shanghai': '上海', 'Guangzhou': '广州', 'Chengdu': '成都', 'Shenzhen': '深圳', 'Other city': '其他城市',
  'Coming from': '从哪里出发', 'Leaving for': '下一目的地', 'Arrival date': '抵达日期', 'Length of stay': '停留时长',
  '1–3 days': '1–3 天', '4–7 days': '4–7 天', '8–14 days': '8–14 天', '15–30 days': '15–30 天', 'More than 30 days': '超过 30 天',
  'TripPal does not issue visa decisions.': 'TripPal 不提供签证裁定。',
  'Your plan will identify what needs official verification for your exact passport and route.': '你的计划会标出哪些事项需要根据护照和具体路线向官方核实。',
  'Step 2 of 4': '第 2 步，共 4 步', 'Will your phone work?': '你的手机能正常使用吗？',
  'Data access and a Chinese phone number solve different problems. Tell us what you have arranged.': '移动数据和中国手机号解决的是不同问题，请告诉我们你已经做了哪些准备。',
  'Primary connection plan': '主要联网方案', 'Carrier roaming': '运营商国际漫游', 'Keep your usual number': '保留原手机号',
  'Travel eSIM': '旅行 eSIM', 'Usually data only': '通常仅提供数据', 'Local SIM': '中国本地 SIM', 'Chinese number and data': '中国手机号与数据',
  'Nothing yet': '尚未准备', 'I still need a plan': '我还需要安排', 'I will have a +86 number': '我会有一个 +86 手机号',
  'Useful for SMS verification and some local services': '可用于短信验证和部分本地服务', 'Essential apps are installed and tested': '必要应用已安装并测试',
  'Payment, local maps, translation and connection tools': '支付、本地地图、翻译与联网工具', 'I saved offline directions and bookings': '我已保存离线路线和预订信息',
  'Addresses, tickets and key screenshots work without data': '地址、票据和关键截图在无网络时也可查看',
  'Step 3 of 4': '第 3 步，共 4 步', 'Build a payment fallback.': '建立支付备用方案。',
  'A wallet that appears configured can still fail. Independent backups matter more than a single perfect setup.': '已经配置的钱包仍可能支付失败；多个独立备用方案比单一方案更可靠。',
  'Mobile payment status': '移动支付状态', 'Not set up': '尚未设置', 'Started, not verified': '已开始，但未验证', 'One wallet tested': '已测试一个钱包',
  'Alipay and WeChat Pay tested': '支付宝与微信支付均已测试', 'Usable physical bank cards': '可用的实体银行卡', 'None': '没有', 'One card': '一张卡',
  'Two or more cards': '两张或更多', 'Main card network': '主要银行卡组织', 'Cash backup': '现金备用方案', 'No cash plan': '没有现金方案',
  'I can withdraw RMB': '我可以提取人民币', 'I will carry some RMB': '我会携带少量人民币', 'Three independent layers': '三个独立层级',
  'Mobile wallet · second card · RMB fallback': '移动钱包 · 第二张卡 · 人民币备用', 'Step 4 of 4': '第 4 步，共 4 步',
  'Stress-test your bookings.': '检查预订中的薄弱环节。', 'Final step': '最后一步',
  'The last check covers passport registration, timed attractions and arrival-day communication.': '最后检查护照登记、限时预约景点和抵达当天的沟通准备。',
  'Accommodation type': '住宿类型', 'International or large hotel': '国际连锁或大型酒店', 'Small local hotel': '本地小型酒店',
  'Guesthouse or homestay': '客栈或民宿', 'Staying with friends': '住在朋友家', 'Not booked yet': '尚未预订', 'Passport check-in confirmed?': '已确认可用外国护照入住？',
  'No written confirmation': '没有书面确认', 'Yes, confirmed in writing': '是，已书面确认', 'Not applicable': '不适用', 'Popular timed attractions?': '计划前往热门预约景点？',
  'No': '否', 'Yes, booked or release date known': '是，已预约或知道放票时间', 'Yes, not checked yet': '是，尚未核查', 'Intercity train planned?': '计划乘坐城际列车？',
  'Yes': '是', 'Accommodation name or Chinese address': '住宿名称或中文地址', 'I will carry my original passport on travel days': '出行当天我会携带护照原件',
  'Copies and photos may not replace the original for identity checks': '复印件或照片可能无法替代原件完成身份核验', 'Back': '返回',
  'Browser preview': '浏览器预览', 'Profile': '档案', 'coverage': '完整度', 'Reset this check': '重置本次检查',
  'This only checks form coverage. TripPal Skill makes the final assessment.': '这里仅检查表单完整度，最终判断由 TripPal Skill 完成。',
  'TripPal Skill handoff': 'TripPal Skill 交接', 'Travel profile ready': '旅行档案已就绪', 'Your answers are ready for Skill analysis.': '你的回答已准备交给 Skill 分析。',
  'The webpage has packaged your choices into a structured travel profile. TripPal Skill can now read it, verify time-sensitive rules and return the finished plan here.': '网页已将你的选项整理为结构化旅行档案。TripPal Skill 可以读取档案、核验时效性规则，并把完整计划返回这里。',
  'Profile submitted': '档案已提交', 'Submission prepared on this device': '已在此设备准备提交', 'Ask TripPal to analyze this page': '请 TripPal 分析此页面',
  'In the assistant, say “Use TripPal to analyze my submitted profile.”': '在助手中说：“使用 TripPal 分析我已提交的档案。”',
  'Result returns here': '结果返回此处', 'The Skill writes risks, actions and official checks back to this page.': 'Skill 会把风险、行动项和官方核验结果写回此页面。',
  'Copy profile for TripPal': '复制档案给 TripPal', 'Waiting for TripPal Skill. Keep this page open.': '正在等待 TripPal Skill，请保持此页面打开。',
  'TripPal Skill assessment': 'TripPal Skill 评估', 'Your route': '你的路线', 'Departure file': '出发档案',
  'Risks': '风险', 'Action plan': '行动计划', 'Official checks': '官方核验', 'Offline cards': '离线卡片', 'Print / Save PDF': '打印 / 保存 PDF',
  '01 · What could break': '01 · 可能出现的问题', 'Your risk register': '你的风险清单', '02 · What to do': '02 · 需要完成的事项',
  '03 · Time-sensitive': '03 · 时效性信息', '04 · Keep on your phone': '04 · 保存在手机中', 'SHOW A DRIVER': '出示给司机', 'Please take me to this address.': '请带我去这个地址。',
  'PAYMENT HELP': '支付求助', 'My mobile payment failed. Can I pay another way?': '我的手机支付失败了，可以用其他方式付款吗？',
  'HOTEL CHECK-IN': '酒店入住', 'I am checking in with a foreign passport.': '我使用外国护照办理入住。',
  'Edit answers': '修改回答', 'Start a new check': '重新检查',
  'This assessment was generated by TripPal Skill from your submitted profile. Time-sensitive conclusions should include an official source and check date.': '此评估由 TripPal Skill 根据你提交的档案生成。时效性结论应包含官方来源和核验日期。',
};

const PLACEHOLDER_ZH = { 'e.g. Los Angeles': '例如：洛杉矶', 'e.g. Tokyo': '例如：东京', 'Used to create your arrival card': '用于生成抵达地址卡' };
const VALUE_ZH = { 'United States': '美国', Canada: '加拿大', 'United Kingdom': '英国', Australia: '澳大利亚', Singapore: '新加坡', Japan: '日本', Other: '其他', Beijing: '北京', Shanghai: '上海', Guangzhou: '广州', Chengdu: '成都', Shenzhen: '深圳', 'Other city': '其他城市' };
const tr = (en, zh) => currentLanguage === 'zh' ? zh : en;
const localizedValue = (value) => currentLanguage === 'zh' ? (VALUE_ZH[value] || value) : value;

function translateStatic() {
  document.documentElement.lang = currentLanguage === 'zh' ? 'zh-CN' : 'en';
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement?.closest('script, style, [data-no-translate]')) continue;
    if (!originalText.has(node)) originalText.set(node, node.nodeValue);
    const source = originalText.get(node);
    if (currentLanguage === 'en') node.nodeValue = source;
    else {
      const trimmed = source.trim();
      node.nodeValue = TEXT_ZH[trimmed] ? source.replace(trimmed, TEXT_ZH[trimmed]) : source;
    }
  }
  document.querySelectorAll('[placeholder]').forEach((element) => {
    if (!element.dataset.placeholderEn) element.dataset.placeholderEn = element.placeholder;
    element.placeholder = currentLanguage === 'zh' ? (PLACEHOLDER_ZH[element.dataset.placeholderEn] || element.dataset.placeholderEn) : element.dataset.placeholderEn;
  });
  document.querySelectorAll('[data-lang]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.lang === currentLanguage)));
}

const labels = {
  high: { en: 'Fix before departure', zh: '出发前处理', code: '!' },
  medium: { en: 'Build a backup', zh: '准备备用方案', code: '2' },
  critical: { en: 'Fix before departure', zh: '出发前处理', code: '!' },
  important: { en: 'Build a backup', zh: '准备备用方案', code: '2' },
  verify: { en: 'Official check', zh: '向官方核实', code: '?' },
};

function formData() {
  const data = Object.fromEntries(new FormData(form).entries());
  for (const name of ['chinaNumber', 'appsReady', 'offlineReady', 'passportOriginal']) {
    data[name] = Boolean(form.elements[name]?.checked);
  }
  return data;
}

function setFormData(data = {}) {
  Object.entries(data).forEach(([name, value]) => {
    const control = form.elements[name];
    if (!control) return;
    if (control instanceof RadioNodeList) {
      [...control].forEach((item) => { item.checked = item.value === value; });
    } else if (control.type === 'checkbox') {
      control.checked = Boolean(value);
    } else {
      control.value = value ?? '';
    }
  });
}

function profileFromForm(data = formData()) {
  return {
    route: {
      passportNationality: data.nationality || '',
      origin: data.origin || '',
      arrivalCity: data.arrival || '',
      onwardDestination: data.onward || '',
      arrivalDate: data.arrivalDate || '',
      stayDays: Number(data.stay || 0),
    },
    connectivity: {
      plan: data.connection || '',
      hasChinaNumber: Boolean(data.chinaNumber),
      appsTested: Boolean(data.appsReady),
      offlinePackReady: Boolean(data.offlineReady),
    },
    payments: {
      walletStatus: data.wallet || 'none',
      physicalCardCount: Number(data.cards || 0),
      mainCardNetwork: data.network || '',
      cashFallback: data.cash || 'no',
    },
    bookings: {
      accommodationType: data.hotelType || 'none',
      foreignPassportCheckInConfirmed: data.hotelConfirmed || 'no',
      timedAttractions: data.attraction || 'no',
      intercityTrainPlanned: data.train === 'yes',
      accommodationAddress: data.address || '',
      willCarryOriginalPassport: Boolean(data.passportOriginal),
    },
  };
}

function missingRequired(profile) {
  const required = [
    ['route.passportNationality', profile.route.passportNationality],
    ['route.origin', profile.route.origin],
    ['route.arrivalCity', profile.route.arrivalCity],
    ['route.onwardDestination', profile.route.onwardDestination],
    ['route.arrivalDate', profile.route.arrivalDate],
    ['connectivity.plan', profile.connectivity.plan],
  ];
  return required.filter(([, value]) => !value).map(([path]) => path);
}

function currentEnvelope() {
  let stored = null;
  try { stored = JSON.parse(localStorage.getItem(SUBMISSION_KEY) || 'null'); } catch { localStorage.removeItem(SUBMISSION_KEY); }
  const profile = profileFromForm();
  return {
    schemaVersion: '1.0',
    status: stored?.status === 'submitted' ? 'submitted' : 'draft',
    submissionId: stored?.submissionId || null,
    submittedAt: stored?.submittedAt || null,
    language: currentLanguage,
    profile,
    missingRequired: missingRequired(profile),
  };
}

function markProfileChanged() {
  localStorage.removeItem(SUBMISSION_KEY);
  localStorage.removeItem(ASSESSMENT_KEY);
}

function analyze(data) {
  const risks = [];
  const add = (id, severity, title, detail, action, timing) => risks.push({ id, severity, title, detail, action, timing });

  add('entry', 'verify', tr('Confirm entry eligibility for this exact route', '确认这条具体路线的入境资格'),
    currentLanguage === 'zh' ? `${localizedValue(data.nationality) || '你的护照'}、${data.origin || '出发地'}、${data.onward || '下一目的地'}以及 ${data.stay || '?'} 天停留需要结合核查。` : `${data.nationality || 'Your passport'}, ${data.origin || 'origin'}, ${data.onward || 'onward destination'} and a ${data.stay || '?'}-day stay must be checked together.`,
    tr('Use an official immigration or consular source; save the rule and supporting itinerary offline.', '使用官方移民或领事来源核查，并将规则和相关行程离线保存。'), tr('Now', '现在'));
  if (!data.connection || data.connection === 'none') add('connection', 'high', tr('No independent connection plan', '缺少独立联网方案'), tr('Airport Wi-Fi or a single local option is not a reliable arrival plan.', '机场 Wi-Fi 或单一的本地方案不足以作为可靠的抵达联网计划。'), tr('Arrange roaming or a travel data plan and test activation before boarding.', '安排国际漫游或旅行数据方案，并在登机前测试激活。'), tr('Now', '现在'));
  if (data.connection === 'esim' && !data.chinaNumber) add('number', 'medium', tr('Data does not equal a Chinese phone number', '有流量不等于有中国手机号'), tr('A travel eSIM may connect you while still leaving SMS-based local services unavailable.', '旅行 eSIM 可以联网，但依赖中国短信验证的本地服务可能仍无法使用。'), tr('Keep your home number active for codes and identify whether any planned service requires +86 SMS.', '保留原手机号接收验证码，并确认计划使用的服务是否要求 +86 短信。'), tr('This week', '本周'));
  if (!data.appsReady) add('apps', 'high', tr('Essential apps are not departure-ready', '必要应用尚未准备就绪'), tr('Some downloads, sign-ins or verification steps may be harder after arrival.', '抵达后，部分下载、登录或验证步骤可能更难完成。'), tr('Install and sign in to connection, payment, local map and translation tools before departure.', '出发前安装并登录联网、支付、本地地图和翻译工具。'), tr('Now', '现在'));
  if (!data.offlineReady) add('offline', 'medium', tr('No offline fallback', '缺少离线备用方案'), tr('A weak connection can hide the address, booking or QR code you need at that moment.', '网络不稳定时，你可能无法查看当下需要的地址、预订或二维码。'), tr('Save booking screenshots, Chinese addresses and essential directions on the device.', '在设备中保存预订截图、中文地址和关键路线。'), tr('24 hours before', '出发前 24 小时'));
  if (!data.wallet || data.wallet === 'none' || data.wallet === 'started') add('wallet', 'high', tr('Mobile payment is not tested', '移动支付尚未测试'), tr('Setup or verification alone does not prove a small payment will work.', '仅完成设置或实名认证，并不能证明实际小额付款一定成功。'), tr('Complete identity checks, link a card and make a test payment if your provider permits it.', '完成身份核验、绑定银行卡，并在服务允许时进行一次测试付款。'), tr('Now', '现在'));
  if (Number(data.cards || 0) < 2) add('cards', 'medium', tr('Too few independent card backups', '独立银行卡备用不足'), tr('One declined, frozen or lost card can remove your only non-cash fallback.', '唯一一张卡被拒、冻结或遗失时，你会失去非现金备用方案。'), tr('Carry a second physical card from a different account when possible.', '条件允许时，携带来自不同账户的第二张实体卡。'), tr('This week', '本周'));
  if (!data.cash || data.cash === 'no') add('cash', 'medium', tr('No RMB fallback', '没有人民币备用'), tr('Cash is not the primary experience, but it remains an independent emergency layer.', '现金虽不是主要支付方式，但仍是独立的应急层。'), tr('Plan a modest RMB reserve or verify an ATM withdrawal route.', '准备少量人民币，或确认可用的 ATM 取现方案。'), tr('Before departure', '出发前'));
  if (['small', 'guesthouse', 'none'].includes(data.hotelType) && data.hotelConfirmed !== 'yes') add('hotel', 'high', tr('Foreign-passport check-in is unconfirmed', '外国护照入住尚未确认'), tr('A booking confirmation does not always confirm that the property can complete your registration.', '预订成功并不一定代表住宿方能够完成外国旅客登记。'), tr('Ask in writing whether the property accepts and registers guests using a foreign passport; keep a backup hotel.', '书面确认住宿方能否接待并登记外国护照旅客，同时准备一家备用酒店。'), tr('Now', '现在'));
  if (data.hotelType === 'friends') add('registration', 'verify', tr('Check temporary residence registration', '核查临时住宿登记'), tr('Private stays can involve a different registration process from hotel stays.', '住在私人住所时，登记流程可能与酒店不同。'), tr('Ask the local responsible authority or your host what is required and by when.', '向当地主管部门或房东确认所需材料和办理时限。'), tr('Before arrival', '抵达前'));
  if (data.attraction === 'unknown') add('attraction', 'high', tr('Timed attraction booking is unresolved', '限时景点预约尚未解决'), tr('Popular sites may use fixed release windows and passport-based reservations.', '热门景点可能采用固定放票时间和护照实名预约。'), tr('Find the official booking channel, release time, identity rules and one substitute attraction.', '确认官方预约渠道、放票时间、证件规则，并准备一个替代景点。'), tr('Now', '现在'));
  if (data.train === 'yes' && !data.passportOriginal) add('train', 'medium', tr('Original passport is not in the travel-day plan', '出行计划中未包含护照原件'), tr('Rail and attraction identity checks may require the original document or a staffed lane.', '铁路和景点身份核验可能要求证件原件或走人工通道。'), tr('Carry the original passport and allow extra time for manual identity checks.', '携带护照原件，并为人工身份核验预留额外时间。'), tr('Travel day', '出行当天'));
  if (!data.address) add('address', 'medium', tr('No Chinese arrival address saved', '尚未保存中文抵达地址'), tr('An English property name may not be enough for a driver or station staff member.', '仅有英文住宿名称，司机或车站工作人员可能无法识别。'), tr('Save the property’s Chinese name, full address, phone number and a nearby landmark.', '保存住宿的中文名称、完整地址、电话和附近地标。'), tr('24 hours before', '出发前 24 小时'));

  const deductions = risks.reduce((sum, risk) => sum + ({ high: 13, medium: 6, verify: 4 }[risk.severity]), 0);
  const score = Math.max(18, Math.min(96, 100 - deductions));
  return { score, risks };
}

function answeredCount(data) {
  const keys = ['nationality', 'arrival', 'origin', 'onward', 'arrivalDate', 'connection', 'address'];
  return keys.filter((key) => data[key]).length;
}

function save() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ data: formData(), currentStep }));
  const status = document.querySelector('#save-status');
  status.textContent = tr('Draft saved just now', '草稿刚刚已保存');
  window.clearTimeout(save.timer);
  save.timer = window.setTimeout(() => { status.textContent = tr('Draft saved on this device', '草稿已保存在此设备'); }, 1400);
}

function renderPreview() {
  const data = formData();
  const analysis = analyze(data);
  const answered = answeredCount(data);
  const started = answered > 1;
  const coverage = Math.round((answered / 7) * 100);
  document.querySelector('#live-score').textContent = started ? coverage : '—';
  document.querySelector('#score-fill').style.width = started ? `${coverage}%` : '18%';
  document.querySelector('#score-copy').textContent = started
    ? tr('This is form coverage, not a readiness score. TripPal Skill will judge the submitted profile.', '这是表单完整度，不是准备度评分。TripPal Skill 将判断提交后的档案。')
    : tr('This only checks form coverage. TripPal Skill makes the final assessment.', '这里仅检查表单完整度，最终判断由 TripPal Skill 完成。');
  document.querySelector('#route-from').textContent = (data.origin || tr('FROM', '出发')).slice(0, 12).toUpperCase();
  document.querySelector('#route-city').textContent = (localizedValue(data.arrival) || tr('CHINA', '中国')).slice(0, 12).toUpperCase();
  document.querySelector('#route-next').textContent = (data.onward || tr('NEXT', '下一站')).slice(0, 12).toUpperCase();

  const topRisks = started ? analysis.risks.filter((r) => r.severity === 'high').slice(0, 2) : [];
  const fallback = analysis.risks.find((r) => r.severity === 'verify');
  const visible = topRisks.length ? topRisks : fallback ? [fallback] : [];
  document.querySelector('#preview-risks').innerHTML = visible.map((risk) => `
    <div class="signal-card compact"><span class="risk-flag ${risk.severity === 'high' ? 'high' : ''}">${labels[risk.severity][currentLanguage]}</span><h2>${risk.title}</h2><p>${risk.detail}</p></div>
  `).join('');
}

function showStep(step, focus = true) {
  currentStep = Math.max(0, Math.min(3, step));
  formSteps.forEach((section, index) => section.classList.toggle('active', index === currentStep));
  stepItems.forEach((item, index) => item.classList.toggle('active', index === currentStep));
  backButton.hidden = currentStep === 0;
  const nextEnglish = ['Continue to connection', 'Continue to money', 'Continue to bookings'];
  const nextChinese = ['继续：网络连接', '继续：支付准备', '继续：预订事项'];
  nextButton.textContent = currentStep === 3 ? tr('Send to TripPal Skill', '提交给 TripPal Skill') : (currentLanguage === 'zh' ? nextChinese : nextEnglish)[currentStep];
  errorBox.textContent = '';
  if (focus) formSteps[currentStep].querySelector('h1')?.focus({ preventScroll: true });
  save();
}

function validateStep() {
  if (currentStep === 0) {
    const required = [...formSteps[0].querySelectorAll('[required]')];
    const missing = required.find((control) => !control.value.trim());
    if (missing) {
      errorBox.textContent = tr('Complete the route fields before continuing.', '请先完整填写路线信息。');
      missing.focus();
      return false;
    }
  }
  if (currentStep === 1 && !formData().connection) {
    errorBox.textContent = tr('Choose your primary connection plan.', '请选择主要联网方案。');
    form.elements.connection[0].focus();
    return false;
  }
  return true;
}

function actionGroups(risks) {
  const order = currentLanguage === 'zh' ? ['现在', '本周', '出发前', '出发前 24 小时', '出行当天', '抵达前'] : ['Now', 'This week', 'Before departure', '24 hours before', 'Travel day', 'Before arrival'];
  const groups = new Map();
  risks.forEach((risk) => {
    const key = risk.timing;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(risk);
  });
  return order.filter((key) => groups.has(key)).map((key) => [key, groups.get(key)]);
}

const escapeHtml = (value = '') => String(value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
const timingLabels = {
  now: { en: 'Now', zh: '现在' }, this_week: { en: 'This week', zh: '本周' },
  before_departure: { en: 'Before departure', zh: '出发前' }, '24h_before': { en: '24 hours before', zh: '出发前 24 小时' },
  arrival_day: { en: 'Arrival day', zh: '抵达当天' }, travel_day: { en: 'Travel day', zh: '出行当天' },
};

function showHandoff(envelope = currentEnvelope()) {
  document.querySelector('#check').hidden = true;
  document.querySelector('#results').hidden = true;
  document.querySelector('#skill-handoff').hidden = false;
  document.querySelector('#handoff-id').textContent = `${tr('Submission', '提交编号')} ${envelope.submissionId}`;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function submitForSkill() {
  const profile = profileFromForm();
  const missing = missingRequired(profile);
  if (missing.length) {
    errorBox.textContent = tr('Complete the route and connection fields before submitting.', '请先完整填写路线和联网信息再提交。');
    showStep(missing[0].startsWith('connectivity') ? 1 : 0);
    return;
  }
  const submittedAt = new Date().toISOString();
  const submission = {
    status: 'submitted',
    submissionId: `trip-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    submittedAt,
  };
  localStorage.setItem(SUBMISSION_KEY, JSON.stringify(submission));
  localStorage.removeItem(ASSESSMENT_KEY);
  showHandoff({ ...currentEnvelope(), ...submission });
}

async function copyProfile() {
  const button = document.querySelector('#copy-profile-button');
  const note = document.querySelector('#handoff-note');
  try {
    await navigator.clipboard.writeText(JSON.stringify(currentEnvelope(), null, 2));
    button.textContent = tr('Profile copied', '档案已复制');
    note.textContent = tr('Paste it into the assistant and ask $trippal to analyze it.', '将其粘贴到助手中，并请 $trippal 进行分析。');
  } catch {
    note.textContent = tr('Clipboard access failed. Ask the assistant to read get_trip_profile from this page.', '无法访问剪贴板。请让助手通过此页面的 get_trip_profile 读取档案。');
  }
}

function safeOfficialUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch { return ''; }
}

function renderSkillAssessment(input) {
  const envelope = currentEnvelope();
  if (envelope.status !== 'submitted') throw new Error('No submitted TripPal profile is available.');
  if (input?.submissionId !== envelope.submissionId) throw new Error('Assessment submissionId does not match the latest submitted profile.');
  if (input?.source !== 'trippal-skill') throw new Error('Assessment source must be trippal-skill.');
  const score = Math.max(0, Math.min(100, Number(input.score)));
  if (!Number.isFinite(score)) throw new Error('Assessment score must be a number from 0 to 100.');
  const risks = Array.isArray(input.risks) ? input.risks.slice(0, 8) : [];
  const actions = Array.isArray(input.actions) ? input.actions.slice(0, 12) : [];
  const checks = Array.isArray(input.officialChecks) ? input.officialChecks : [];
  const assumptions = Array.isArray(input.assumptions) ? input.assumptions : [];
  const data = formData();

  document.querySelector('#check').hidden = true;
  document.querySelector('#skill-handoff').hidden = true;
  document.querySelector('#results').hidden = false;
  document.querySelector('#result-score').textContent = Math.round(score);
  document.querySelector('#result-summary').textContent = input.summary || tr('TripPal Skill completed the assessment.', 'TripPal Skill 已完成评估。');
  const generated = input.generatedAt ? new Date(input.generatedAt) : new Date();
  document.querySelector('#skill-result-meta').textContent = `${tr('Generated by TripPal Skill', '由 TripPal Skill 生成')} · ${generated.toLocaleString(currentLanguage === 'zh' ? 'zh-CN' : 'en-US')}`;
  document.querySelector('#result-route').textContent = `${data.origin} · ${localizedValue(data.arrival)} · ${data.onward}`;
  document.querySelector('#result-date').textContent = data.arrivalDate ? `${tr('Arriving', '抵达')} ${new Date(`${data.arrivalDate}T12:00:00`).toLocaleDateString(currentLanguage === 'zh' ? 'zh-CN' : 'en-US', { day: 'numeric', month: 'short', year: 'numeric' })}` : tr('Departure file', '出发档案');
  document.querySelector('#offline-address').textContent = data.address || tr('Add your hotel address in the check.', '请在检查中添加酒店地址。');

  document.querySelector('#risk-list').innerHTML = risks.map((risk) => {
    const severity = ['critical', 'important', 'verify'].includes(risk.severity) ? risk.severity : 'verify';
    return `<article class="risk-item ${severity}"><span class="risk-icon">${labels[severity].code}</span><div><h3>${escapeHtml(risk.title)}</h3><p>${escapeHtml(risk.detail)}</p>${risk.evidence ? `<small class="risk-evidence">${escapeHtml(risk.evidence)}</small>` : ''}</div></article>`;
  }).join('') || `<p class="empty-result">${tr('No material risk was returned by the Skill.', 'Skill 未返回重大风险。')}</p>`;

  const completed = JSON.parse(localStorage.getItem(`${STORAGE_KEY}-tasks`) || '{}');
  const grouped = new Map();
  actions.forEach((action) => {
    const timing = timingLabels[action.timing] ? action.timing : 'before_departure';
    if (!grouped.has(timing)) grouped.set(timing, []);
    grouped.get(timing).push(action);
  });
  document.querySelector('#action-groups').innerHTML = [...grouped.entries()].map(([timing, items], index) => `
    <article class="action-group"><span>${String(index + 1).padStart(2, '0')} · ${timingLabels[timing][currentLanguage]}</span><h3>${timing === 'now' ? tr('Remove the blockers', '先解决阻塞项') : tr('Strengthen your backup', '加强备用方案')}</h3>
      ${items.map((item) => `<label class="action-item"><input type="checkbox" data-task="${escapeHtml(item.id)}" ${completed[item.id] ? 'checked' : ''} /><span>${escapeHtml(item.title)}<small>${escapeHtml(item.detail)}</small></span></label>`).join('')}
    </article>`).join('') || `<p class="empty-result">${tr('No action was returned by the Skill.', 'Skill 未返回行动项。')}</p>`;

  document.querySelector('#official-check-list').innerHTML = checks.map((check) => {
    const url = safeOfficialUrl(check.url);
    return `<article class="official-check"><div><b>${escapeHtml(check.topic)}</b><span>${escapeHtml(check.checkedAt || '')}</span></div><p>${escapeHtml(check.result)}</p>${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${tr('Open official source', '打开官方来源')} ↗</a>` : ''}</article>`;
  }).join('') || `<p class="empty-result">${tr('No official check was attached. Treat time-sensitive conclusions as unresolved.', '未附加官方核验信息，请将时效性结论视为待核实。')}</p>`;
  document.querySelector('#assumption-list').innerHTML = assumptions.length ? `<h3>${tr('Assumptions', '假设')}</h3><ul>${assumptions.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '';

  localStorage.setItem(ASSESSMENT_KEY, JSON.stringify(input));
  document.querySelectorAll('[data-task]').forEach((box) => box.addEventListener('change', persistTasks));
  window.scrollTo({ top: 0, behavior: 'smooth' });
  return { displayed: true, submissionId: envelope.submissionId, riskCount: risks.length, actionCount: actions.length };
}

function persistTasks() {
  const state = {};
  document.querySelectorAll('[data-task]').forEach((box) => { state[box.dataset.task] = box.checked; });
  localStorage.setItem(`${STORAGE_KEY}-tasks`, JSON.stringify(state));
}

function resetAll() {
  if (!window.confirm(tr('Clear this readiness check and start again?', '清除本次准备检查并重新开始？'))) return;
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(`${STORAGE_KEY}-tasks`);
  localStorage.removeItem(SUBMISSION_KEY);
  localStorage.removeItem(ASSESSMENT_KEY);
  form.reset();
  document.querySelector('#results').hidden = true;
  document.querySelector('#skill-handoff').hidden = true;
  document.querySelector('#check').hidden = false;
  showStep(0, false);
  renderPreview();
}

form.addEventListener('input', () => { markProfileChanged(); save(); renderPreview(); });
form.addEventListener('change', () => { markProfileChanged(); save(); renderPreview(); });
nextButton.addEventListener('click', () => {
  if (!validateStep()) return;
  if (currentStep < 3) showStep(currentStep + 1);
  else submitForSkill();
});
backButton.addEventListener('click', () => showStep(currentStep - 1));
stepItems.forEach((item) => item.querySelector('button').addEventListener('click', () => showStep(Number(item.dataset.step))));
document.querySelector('#reset-button').addEventListener('click', resetAll);
document.querySelector('#result-reset-button').addEventListener('click', resetAll);
document.querySelector('#edit-button').addEventListener('click', () => {
  document.querySelector('#results').hidden = true;
  document.querySelector('#check').hidden = false;
  showStep(3, false);
  window.scrollTo({ top: 0, behavior: 'smooth' });
});
document.querySelector('#handoff-edit-button').addEventListener('click', () => {
  document.querySelector('#skill-handoff').hidden = true;
  document.querySelector('#check').hidden = false;
  showStep(3, false);
  window.scrollTo({ top: 0, behavior: 'smooth' });
});
document.querySelector('#copy-profile-button').addEventListener('click', copyProfile);
document.querySelector('#print-button').addEventListener('click', () => window.print());

function setLanguage(language) {
  const assessment = JSON.parse(localStorage.getItem(ASSESSMENT_KEY) || 'null');
  currentLanguage = language === 'zh' ? 'zh' : 'en';
  localStorage.setItem(LANGUAGE_KEY, currentLanguage);
  translateStatic();
  showStep(currentStep, false);
  renderPreview();
  if (assessment && !document.querySelector('#results').hidden) renderSkillAssessment(assessment);
}

document.querySelectorAll('[data-lang]').forEach((button) => {
  button.addEventListener('click', () => setLanguage(button.dataset.lang));
});

translateStatic();

try {
  const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
  setFormData(saved.data);
  showStep(Number.isInteger(saved.currentStep) ? saved.currentStep : 0, false);
} catch {
  showStep(0, false);
}
renderPreview();
try {
  const restoredAssessment = JSON.parse(localStorage.getItem(ASSESSMENT_KEY) || 'null');
  const restoredEnvelope = currentEnvelope();
  if (restoredAssessment && restoredEnvelope.status === 'submitted' && restoredAssessment.submissionId === restoredEnvelope.submissionId) {
    renderSkillAssessment(restoredAssessment);
  } else if (restoredEnvelope.status === 'submitted') {
    showHandoff(restoredEnvelope);
  }
} catch {
  localStorage.removeItem(ASSESSMENT_KEY);
}

function registerWebMcp() {
  const context = document.modelContext;
  if (!context?.registerTool) return;
  const fields = ['nationality', 'arrival', 'origin', 'onward', 'arrivalDate', 'stay', 'connection', 'chinaNumber', 'appsReady', 'offlineReady', 'wallet', 'cards', 'network', 'cash', 'hotelType', 'hotelConfirmed', 'attraction', 'train', 'address', 'passportOriginal'];
  try {
    context.registerTool({
      name: 'configure_trip_profile',
      title: 'Configure TripPal profile',
      description: 'Fill supported TripPal intake fields. This updates the draft only and does not perform the Skill assessment.',
      inputSchema: { type: 'object', properties: Object.fromEntries(fields.map((key) => [key, { type: ['string', 'boolean'] }])), additionalProperties: false },
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      execute(input) {
        const accepted = Object.fromEntries(Object.entries(input || {}).filter(([key]) => fields.includes(key)));
        setFormData({ ...formData(), ...accepted });
        markProfileChanged(); save(); renderPreview();
        return { updated: Object.keys(accepted), status: 'draft', missingRequired: currentEnvelope().missingRequired };
      },
    });
    context.registerTool({
      name: 'get_trip_profile',
      title: 'Read submitted TripPal profile',
      description: 'Read the structured travel profile submitted on this page for analysis with $trippal. Analyze only when status is submitted unless the user explicitly requests a draft review.',
      inputSchema: { type: 'object', properties: {}, additionalProperties: false },
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute() {
        return currentEnvelope();
      },
    });
    context.registerTool({
      name: 'render_skill_assessment',
      title: 'Return TripPal Skill assessment',
      description: 'Render a completed $trippal assessment into the webpage. Use only after reading the latest submitted profile; preserve its submissionId.',
      inputSchema: {
        type: 'object',
        required: ['submissionId', 'source', 'generatedAt', 'language', 'score', 'status', 'summary', 'risks', 'actions', 'officialChecks', 'assumptions'],
        properties: {
          submissionId: { type: 'string' }, source: { type: 'string', const: 'trippal-skill' }, generatedAt: { type: 'string' },
          language: { type: 'string', enum: ['en', 'zh'] }, score: { type: 'number', minimum: 0, maximum: 100 },
          status: { type: 'string', enum: ['needs_attention', 'mostly_ready', 'ready'] }, summary: { type: 'string' },
          risks: { type: 'array', maxItems: 8, items: { type: 'object', required: ['id', 'severity', 'title', 'detail'], properties: { id: { type: 'string' }, severity: { type: 'string', enum: ['critical', 'important', 'verify'] }, title: { type: 'string' }, detail: { type: 'string' }, evidence: { type: 'string' } }, additionalProperties: false } },
          actions: { type: 'array', maxItems: 12, items: { type: 'object', required: ['id', 'timing', 'title', 'detail'], properties: { id: { type: 'string' }, timing: { type: 'string', enum: ['now', 'this_week', 'before_departure', '24h_before', 'arrival_day', 'travel_day'] }, title: { type: 'string' }, detail: { type: 'string' } }, additionalProperties: false } },
          officialChecks: { type: 'array', items: { type: 'object', required: ['topic', 'result', 'checkedAt'], properties: { topic: { type: 'string' }, result: { type: 'string' }, url: { type: 'string' }, checkedAt: { type: 'string' } }, additionalProperties: false } },
          assumptions: { type: 'array', items: { type: 'string' } },
        },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      execute(input) {
        return renderSkillAssessment(input);
      },
    });
  } catch (error) {
    console.info('WebMCP tools unavailable', error);
  }
}
registerWebMcp();
