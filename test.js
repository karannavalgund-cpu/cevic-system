
let supabase;
try {
  const SUPABASE_URL = 'https://wzlqorsyweibmknewxnt.supabase.co';
  const SUPABASE_KEY = 'sb_publishable_O_4N8pvqdv-oZDD5MVYgtA_cgk4zIgq'; // User's provided publishable key
  supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY);
} catch (initErr) {
  alert("CRITICAL ERROR: Supabase failed to initialize. Your API key might be completely invalid! Error: " + initErr.message);
  console.error("Supabase Init Error:", initErr);
}

let currentOfficer = null;

// UI Helpers
function showToast(msg, type = 'info') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = `toast ${type} show`;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 3500);
}

function toggleAuth(view) {
  document.getElementById('login-card').style.display = view === 'login' ? 'block' : 'none';
  document.getElementById('signup-card').style.display = view === 'signup' ? 'block' : 'none';
}

function updateUI() {
  if (currentOfficer) {
    document.getElementById('auth-container').style.display = 'none';
    document.getElementById('dashboard-container').style.display = 'block';
    document.getElementById('logout-btn').style.display = 'inline-block';
    
    const meta = currentOfficer.user_metadata || {};
    document.getElementById('officer-info-display').textContent = `Welcome, ${meta.name || currentOfficer.email}`;
    document.getElementById('officer-area-display').textContent = meta.area || 'All Areas';
    
    fetchComplaints();
  } else {
    document.getElementById('auth-container').style.display = 'block';
    document.getElementById('dashboard-container').style.display = 'none';
    document.getElementById('logout-btn').style.display = 'none';
  }
}

// Auth Logic
async function checkUser() {
  const { data: { session } } = await supabase.auth.getSession();
  if (session) {
    currentOfficer = session.user;
  }
  updateUI();
}

document.getElementById('signup-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector('button');
  const originalText = btn.textContent;
  btn.textContent = 'Please wait...';
  btn.disabled = true;

  try {
    const name = document.getElementById('signup-name').value;
    const email = document.getElementById('signup-email').value;
    const password = document.getElementById('signup-password').value;
    const area = document.getElementById('signup-area').value;
    const position = document.getElementById('signup-position').value;

    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: { name, area, position }
      }
    });

    if (error) {
      showToast(error.message, 'error');
    } else {
      showToast('Registration successful! Please log in.', 'success');
      toggleAuth('login');
    }
  } catch (err) {
    showToast('Unexpected error: ' + err.message, 'error');
  } finally {
    btn.textContent = originalText;
    btn.disabled = false;
  }
});

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector('button');
  const originalText = btn.textContent;
  btn.textContent = 'Please wait...';
  btn.disabled = true;

  try {
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;

    const { data, error } = await supabase.auth.signInWithPassword({ email, password });

    if (error) {
      showToast(error.message, 'error');
    } else {
      currentOfficer = data.user;
      showToast('Logged in successfully', 'success');
      updateUI();
    }
  } catch (err) {
    showToast('Unexpected error: ' + err.message, 'error');
  } finally {
    btn.textContent = originalText;
    btn.disabled = false;
  }
});

document.getElementById('logout-btn').addEventListener('click', async () => {
  await supabase.auth.signOut();
  currentOfficer = null;
  updateUI();
  showToast('Logged out', 'success');
});

// Dashboard Logic
async function fetchComplaints() {
  const grid = document.getElementById('complaints-grid');
  grid.innerHTML = '<p style="grid-column: 1/-1; text-align:center;">Loading complaints...</p>';

  // In a real app, we would filter by jurisdiction. Since data might not match perfectly,
  // we will fetch all active complaints and let the officer see them.
  const { data, error } = await supabase
    .from('complaints')
    .select('*')
    .order('timestamp', { ascending: false });

  if (error) {
    grid.innerHTML = `<p style="color:red;">Error loading complaints: ${error.message}</p>`;
    return;
  }

  if (!data || data.length === 0) {
    grid.innerHTML = '<p style="grid-column: 1/-1; text-align:center;">No complaints found.</p>';
    return;
  }

  grid.innerHTML = '';
  data.forEach(complaint => {
    const card = document.createElement('div');
    card.className = 'complaint-card';
    
    const date = new Date(complaint.timestamp).toLocaleDateString('en-IN');
    const imgSrc = complaint.image_url || 'https://via.placeholder.com/300x150?text=No+Evidence+Provided';

    card.innerHTML = `
      <div class="complaint-header">
        <span>ID: ${complaint.complaint_id}</span>
        <span class="complaint-status status-${complaint.status}">${complaint.status}</span>
      </div>
      <img src="${imgSrc}" class="complaint-img" alt="Complaint Evidence" onerror="this.src='https://via.placeholder.com/300x150?text=Image+Load+Error'">
      <div class="complaint-title">${complaint.category} Issue</div>
      <div class="complaint-desc">${complaint.summary}</div>
      <div style="font-size: 0.75rem; color: var(--muted); margin-top: 5px;">📍 ${complaint.latitude}, ${complaint.longitude}</div>
      <div class="complaint-actions">
        ${complaint.status === 'ASSIGNED' || complaint.status === 'OPEN' ? `
          <button class="btn-action btn-resolve" onclick="resolveComplaint('${complaint.complaint_id}')">Mark Solved</button>
          <button class="btn-action btn-reject" onclick="rejectComplaint('${complaint.complaint_id}')">Not Resolved</button>
        ` : `<span style="font-size: 0.8rem; color: var(--muted); text-align:center; width:100%;">Status updated</span>`}
      </div>
    `;
    grid.appendChild(card);
  });
}

async function resolveComplaint(id) {
  if (!confirm('Mark this complaint as solved?')) return;
  const { error } = await supabase
    .from('complaints')
    .update({ status: 'PENDING_VERIFICATION', updated_at: new Date().toISOString() })
    .eq('complaint_id', id);

  if (error) {
    showToast('Failed to resolve: ' + error.message, 'error');
  } else {
    showToast('Complaint marked as solved! Awaiting community verification.', 'success');
    fetchComplaints();
  }
}

async function rejectComplaint(id) {
  if (!confirm('Mark this complaint as not resolved?')) return;
  const { error } = await supabase
    .from('complaints')
    .update({ status: 'REOPENED', updated_at: new Date().toISOString() })
    .eq('complaint_id', id);

  if (error) {
    showToast('Failed to update: ' + error.message, 'error');
  } else {
    showToast('Complaint marked as not resolved.', 'success');
    fetchComplaints();
  }
}

// Initial Check
checkUser();
