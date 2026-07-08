const STORAGE_KEY = 'fake-survey-form-v1';
const sections = [...document.querySelectorAll('.survey-section')];
const navLinks = [...document.querySelectorAll('.nav-link')];
const form = document.getElementById('surveyForm');
const runStatus = document.getElementById('runStatus');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
const autosaveStatus = document.getElementById('autosaveStatus');
const assessmentList = document.getElementById('assessmentList');
const toast = document.getElementById('toast');
const finalSubmitBtn = document.getElementById('finalSubmit');

let currentSectionIndex = 0;
let saveTimer = null;

function setRunStatus(status) {
  const map = {
    Draft: 'pill pill-draft',
    'Ready for Review': 'pill pill-review',
    'Ready for Submit': 'pill pill-ready',
    Submitted: 'pill pill-submitted'
  };
  runStatus.textContent = status;
  runStatus.className = map[status] || 'pill pill-draft';
}

function showToast(message) {
  toast.textContent = message;
  toast.show();
  setTimeout(() => {
    try { toast.close(); } catch (e) {}
  }, 1200);
}

function sectionIdByIndex(index) {
  return sections[index].id;
}

function activateSection(index) {
  currentSectionIndex = Math.max(0, Math.min(index, sections.length - 1));
  sections.forEach((section, idx) => section.classList.toggle('active', idx === currentSectionIndex));
  navLinks.forEach((btn, idx) => btn.classList.toggle('active', idx === currentSectionIndex));
  updateProgress();
  validateFinalSubmitState();
}

function serializeForm() {
  const data = {};
  const formData = new FormData(form);
  const checkboxGroups = {};

  [...form.elements].forEach((el) => {
    if (!el.name) return;
    if ((el.type === 'checkbox' || el.type === 'radio') && !formData.has(el.name)) {
      if (el.type === 'checkbox') checkboxGroups[el.name] = checkboxGroups[el.name] || [];
      else data[el.name] = '';
    }
  });

  for (const [key, value] of formData.entries()) {
    const field = form.elements[key];
    if (field instanceof RadioNodeList) {
      if (field.length && field[0]?.type === 'checkbox') {
        checkboxGroups[key] = checkboxGroups[key] || [];
        checkboxGroups[key].push(value);
      } else {
        data[key] = value;
      }
    } else if (field?.type === 'checkbox') {
      checkboxGroups[key] = checkboxGroups[key] || [];
      checkboxGroups[key].push(value);
    } else {
      data[key] = value;
    }
  }

  Object.entries(checkboxGroups).forEach(([key, values]) => {
    data[key] = values;
  });

  data.__meta = {
    currentSectionIndex,
    runStatus: runStatus.textContent,
    savedAt: new Date().toISOString()
  };

  return data;
}

function hydrateForm(data) {
  if (!data) return;
  Object.entries(data).forEach(([key, value]) => {
    if (key === '__meta') return;
    const field = form.elements[key];
    if (!field) return;

    if (field instanceof RadioNodeList) {
      const entries = [...field];
      if (entries[0]?.type === 'checkbox') {
        entries.forEach((input) => { input.checked = Array.isArray(value) && value.includes(input.value); });
      } else {
        entries.forEach((input) => { input.checked = input.value === value; });
      }
      return;
    }

    if (field.type === 'checkbox') {
      field.checked = Array.isArray(value) ? value.includes(field.value) : Boolean(value);
    } else {
      field.value = value;
    }
  });

  if (data.__meta?.runStatus) setRunStatus(data.__meta.runStatus);
  if (Number.isInteger(data.__meta?.currentSectionIndex)) {
    activateSection(data.__meta.currentSectionIndex);
  }
}

function clearFormValues() {
  [...form.elements].forEach((field) => {
    if (!field.name) return;

    if (field.type === 'checkbox' || field.type === 'radio') {
      field.checked = false;
      return;
    }

    field.value = '';
  });

  setRunStatus('Draft');
  currentSectionIndex = 0;
  progressFill.style.width = '0%';
  progressText.textContent = `0 / ${sections.length} sections reviewed`;
  autosaveStatus.textContent = 'Autosave idle';
  assessmentList.innerHTML = '';
  finalSubmitBtn.disabled = true;
}

function saveDraft() {
  const data = serializeForm();
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  autosaveStatus.textContent = `Saved ${new Date().toLocaleTimeString()}`;
  showToast('Draft saved');
}

function scheduleAutosave() {
  autosaveStatus.textContent = 'Autosaving...';
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(serializeForm()));
    autosaveStatus.textContent = `Autosaved ${new Date().toLocaleTimeString()}`;
  }, 350);
}

function exportJson() {
  const blob = new Blob([JSON.stringify(serializeForm(), null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'fake-survey-form-data.json';
  a.click();
  URL.revokeObjectURL(url);
}

function resetForm() {
  localStorage.removeItem(STORAGE_KEY);
  clearFormValues();
}

function reviewedSectionCount() {
  const requiredChecks = [
    form.elements['verification_accuracy']?.checked,
    form.elements['verification_authorized']?.checked,
    form.elements['analyst_review_complete']?.checked,
    form.elements['assessment_resolved']?.checked
  ].filter(Boolean).length;
  const base = Math.min(currentSectionIndex + 1, sections.length - 1);
  return Math.min(base + (requiredChecks > 0 ? 1 : 0), sections.length);
}

function updateProgress() {
  const reviewed = reviewedSectionCount();
  const total = sections.length;
  progressFill.style.width = `${(reviewed / total) * 100}%`;
  progressText.textContent = `${reviewed} / ${total} sections reviewed`;
}

function buildAssessmentChecks() {
  const checks = [];
  const totalUg = Number(form.elements['total_undergraduates']?.value || 0);
  const totalGrad = Number(form.elements['total_graduates']?.value || 0);
  const grandTotal = Number(form.elements['grand_total_enrollment']?.value || 0);
  checks.push({
    label: 'Enrollment total matches undergraduate + graduate totals',
    pass: totalUg + totalGrad === grandTotal,
    detail: `${totalUg} + ${totalGrad} ${totalUg + totalGrad === grandTotal ? '=' : '≠'} ${grandTotal}`
  });

  const alumniOfRecord = Number(form.elements['alumni_of_record']?.value || 0);
  const alumniDonors = Number(form.elements['alumni_donors']?.value || 0);
  const rate = Number(form.elements['alumni_giving_rate']?.value || 0);
  const expectedRate = alumniOfRecord ? Number(((alumniDonors / alumniOfRecord) * 100).toFixed(1)) : 0;
  checks.push({
    label: 'Alumni giving rate is consistent with donors / alumni of record',
    pass: Math.abs(expectedRate - rate) <= 0.1,
    detail: `Expected ${expectedRate}% from ${alumniDonors} / ${alumniOfRecord}`
  });

  const gradC = Number(form.elements['grad_c_total']?.value || form.querySelector('[data-testid="grad-c-total"]')?.value || 0);
  const gradG = Number(form.querySelector('[data-testid="grad-g-total"]')?.value || 0);
  const gradRate = Number(form.querySelector('[data-testid="grad-rate-total"]')?.value || 0);
  const expectedGradRate = gradC ? Math.round((gradG / gradC) * 100) : 0;
  checks.push({
    label: 'Six-year graduation rate matches total graduates / final cohort',
    pass: expectedGradRate === gradRate,
    detail: `Expected ${expectedGradRate}% from ${gradG} / ${gradC}`
  });

  const assessmentResolved = form.elements['assessment_resolved']?.checked;
  checks.push({
    label: 'Assessment flags resolved before final submit',
    pass: Boolean(assessmentResolved),
    detail: assessmentResolved ? 'Resolved' : 'Not yet resolved'
  });

  assessmentList.innerHTML = '';
  checks.forEach((item) => {
    const li = document.createElement('li');
    li.className = `assessment-item ${item.pass ? 'pass' : 'warn'}`;
    li.innerHTML = `<strong>${item.pass ? 'PASS' : 'CHECK'} - ${item.label}</strong><span>${item.detail}</span>`;
    assessmentList.appendChild(li);
  });

  const allPass = checks.every((c) => c.pass);
  if (allPass && runStatus.textContent !== 'Submitted') setRunStatus('Ready for Submit');
  return allPass;
}

function validateFinalSubmitState() {
  const requiredFieldsValid = form.checkValidity();
  const verificationChecks = [
    'verification_accuracy',
    'verification_authorized',
    'analyst_review_complete',
    'assessment_resolved'
  ].every((name) => form.elements[name]?.checked);

  const allAssessmentPass = buildAssessmentChecks();
  finalSubmitBtn.disabled = !(requiredFieldsValid && verificationChecks && allAssessmentPass && runStatus.textContent !== 'Submitted');
}

navLinks.forEach((btn, idx) => btn.addEventListener('click', () => activateSection(idx)));

document.getElementById('prevSection').addEventListener('click', () => activateSection(currentSectionIndex - 1));
document.getElementById('nextSection').addEventListener('click', () => activateSection(currentSectionIndex + 1));

['saveDraft', 'saveDraftTop'].forEach((id) => document.getElementById(id).addEventListener('click', saveDraft));
['exportJsonTop'].forEach((id) => document.getElementById(id).addEventListener('click', exportJson));
['resetFormTop'].forEach((id) => document.getElementById(id).addEventListener('click', resetForm));

document.getElementById('markReadyForReview').addEventListener('click', () => {
  setRunStatus('Ready for Review');
  saveDraft();
});

document.getElementById('submitForReview').addEventListener('click', () => {
  setRunStatus('Ready for Review');
  activateSection(sections.findIndex((s) => s.id === 'assessment'));
  showToast('Moved to review state');
});

finalSubmitBtn.addEventListener('click', () => {
  if (finalSubmitBtn.disabled) return;
  setRunStatus('Submitted');
  saveDraft();
  finalSubmitBtn.disabled = true;
  showToast('Fake survey submitted');
});

let _validateTimer = null;
function scheduleValidation() {
  clearTimeout(_validateTimer);
  _validateTimer = setTimeout(validateFinalSubmitState, 800);
}

form.addEventListener('input', () => {
  scheduleAutosave();
  updateProgress();
  scheduleValidation();
});
form.addEventListener('change', () => {
  scheduleAutosave();
  updateProgress();
  scheduleValidation();
});

async function loadInitialDraft() {
  localStorage.removeItem(STORAGE_KEY);
  clearFormValues();
}

loadInitialDraft().finally(() => {
});
