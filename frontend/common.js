// 澶氫粨搴撶鐞嗙郴缁熼€氱敤鑴氭湰

// API鍩虹URL
const API_BASE_URL = 'api';

// 褰撳墠鐢ㄦ埛淇℃伅
let currentUser = null;
let userWarehouses = [];
let currentWarehouse = null;

// 椤甸潰鍔犺浇瀹屾垚鍚庢墽琛?
document.addEventListener('DOMContentLoaded', function() {
    // 鍒濆鍖栭€氱敤鍔熻兘
    initCommon();
});

// 鍒濆鍖栭€氱敤鍔熻兘
function initCommon() {
    // 妫€鏌ョ櫥褰曠姸鎬?
    checkLoginStatus();
    
    // 璁剧疆渚ц竟鏍忓垏鎹?
    setupSidebarToggle();
    
    // 璁剧疆浠撳簱閫夋嫨鍣?
    setupWarehouseSelector();
    
    // 璁剧疆鐢ㄦ埛鑿滃崟
    setupUserMenu();
    
    // 璁剧疆閫€鍑虹櫥褰?
    setupLogout();

    // 注入 PageAgent（使用后端代理，不暴露 OpenAI Key）
    initPageAgent();
}


// 妫€鏌ョ櫥褰曠姸鎬?
function getStoredAuth() {
    const readAuth = (storage) => ({
        token: storage.getItem('access_token'),
        userInfo: storage.getItem('user'),
        expiry: storage.getItem('token_expiry')
    });
    const isComplete = (auth) => !!(auth.token && auth.userInfo);
    const isExpired = (auth) => !!(auth.expiry && new Date() >= new Date(auth.expiry));

    const pickBest = (localAuth, sessionAuth) => {
        if (isComplete(localAuth) && !isExpired(localAuth)) return localAuth;
        if (isComplete(sessionAuth) && !isExpired(sessionAuth)) return sessionAuth;
        if (isComplete(localAuth)) return localAuth;
        if (isComplete(sessionAuth)) return sessionAuth;
        return null;
    };

    const localAuth = readAuth(localStorage);
    const sessionAuth = readAuth(sessionStorage);
    const ownAuth = pickBest(localAuth, sessionAuth);
    if (ownAuth) {
        return ownAuth;
    }

    if (isEmbeddedMode()) {
        try {
            if (window.top && window.top !== window && window.top.location.origin === window.location.origin) {
                const topLocalAuth = readAuth(window.top.localStorage);
                const topSessionAuth = readAuth(window.top.sessionStorage);
                const topAuth = pickBest(topLocalAuth, topSessionAuth);
                if (topAuth) {
                    return topAuth;
                }
            }
        } catch (error) {
            // ignore cross-context access errors
        }
    }

    const legacyToken = localStorage.getItem('token');
    if (legacyToken) {
        localStorage.setItem('access_token', legacyToken);
        localStorage.removeItem('token');
        return {
            token: legacyToken,
            userInfo: localStorage.getItem('user'),
            expiry: localStorage.getItem('token_expiry')
        };
    }

    return { token: null, userInfo: null, expiry: null };
}

// ??????
function checkLoginStatus() {
    const { token, userInfo, expiry } = getStoredAuth();

    if (!token || !userInfo) {
        // ??????????
        window.location.href = 'index.html';
        return;
    }

    if (expiry && new Date() >= new Date(expiry)) {
        logout();
        return;
    }

    try {
        currentUser = JSON.parse(userInfo);
        userWarehouses = currentUser.warehouses || [];

        // ????????
        updateUserDisplay();

        // ??????
        if (currentUser.current_warehouse_id) {
            currentWarehouse = userWarehouses.find(w => w.id === currentUser.current_warehouse_id);
            updateWarehouseDisplay();
        }

        // ???????????????????
        if (getCurrentUserRole() !== 'admin') {
            const warehouseNavLinks = document.querySelectorAll('a[href="warehouse.html"]');
            warehouseNavLinks.forEach(link => {
                link.style.display = 'none';
            });
        }

        // ???????
        if (typeof pageInit === 'function') {
            pageInit();
        }
    } catch (error) {
        console.error('????????:', error);
        logout();
    }
}


// 鏇存柊鐢ㄦ埛淇℃伅鏄剧ず
function updateUserDisplay() {
    if (currentUser) {
        // 鏇存柊鐢ㄦ埛澶村儚棣栧瓧姣?
        const initials = currentUser.full_name ? currentUser.full_name.charAt(0).toUpperCase() : 'U';
        document.getElementById('userInitials').textContent = initials;
        
        // 鏇存柊鐢ㄦ埛鍚?
        document.getElementById('userName').textContent = currentUser.full_name || currentUser.username;
    }
}

// 鏇存柊浠撳簱鏄剧ず
function updateWarehouseDisplay() {
    if (currentWarehouse) {
        document.getElementById('currentWarehouse').textContent = currentWarehouse.name;
    } else if (userWarehouses.length > 0) {
        currentWarehouse = userWarehouses[0];
        document.getElementById('currentWarehouse').textContent = currentWarehouse.name;
    } else {
        document.getElementById('currentWarehouse').textContent = '无可用仓库';
    }
}

// 璁剧疆渚ц竟鏍忓垏鎹?
function setupSidebarToggle() {
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');
    
    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', function() {
            sidebar.classList.toggle('hidden');
        });
    }
}

// 璁剧疆浠撳簱閫夋嫨鍣?
function setupWarehouseSelector() {
    const selector = document.getElementById('warehouseSelector');
    const dropdown = document.getElementById('warehouseDropdown');
    const list = document.getElementById('warehouseList');
    
    if (!selector || !dropdown || !list) return;
    
    // 鐐瑰嚮閫夋嫨鍣ㄥ垏鎹笅鎷夋
    selector.addEventListener('click', function(e) {
        e.stopPropagation();
        dropdown.classList.toggle('hidden');
        updateWarehouseList();
    });
    
    // 鐐瑰嚮鍏朵粬鍦版柟鍏抽棴涓嬫媺妗?
    document.addEventListener('click', function() {
        dropdown.classList.add('hidden');
    });
    
    // 闃绘涓嬫媺妗嗗唴鐐瑰嚮浜嬩欢鍐掓场
    dropdown.addEventListener('click', function(e) {
        e.stopPropagation();
    });
}

// 鏇存柊浠撳簱鍒楄〃
function updateWarehouseList() {
    const list = document.getElementById('warehouseList');
    
    if (!list) return;
    
    list.innerHTML = '';
    
    if (userWarehouses.length === 0) {
        list.innerHTML = `
            <div class="px-4 py-2 text-sm text-gray-500">
                鏃犲彲鐢ㄤ粨搴?
            </div>
        `;
        return;
    }
    
    userWarehouses.forEach(warehouse => {
        const item = document.createElement('div');
        item.className = `px-4 py-2 text-sm cursor-pointer hover:bg-gray-100 ${currentWarehouse && currentWarehouse.id === warehouse.id ? 'bg-primary text-white' : 'text-gray-700'}`;
        item.innerHTML = `
            <div class="flex items-center justify-between">
                <span>${warehouse.name}</span>
                ${warehouse.is_default ? '<span class="text-xs bg-green-100 text-green-800 px-2 py-0.5 rounded">榛樿</span>' : ''}
            </div>
            <div class="text-xs text-gray-500">${warehouse.code}</div>
        `;
        
        item.addEventListener('click', function() {
            switchWarehouse(warehouse.id);
        });
        
        list.appendChild(item);
    });
}

// 鍒囨崲浠撳簱
async function switchWarehouse(warehouseId) {
    try {
        const params = new URLSearchParams({ warehouse_id: warehouseId });
        const response = await fetch(`${API_BASE_URL}/users/${currentUser.id}/switch-warehouse?${params.toString()}`, {
            method: 'POST',
            headers: getHeaders()
        });
        
        if (!response.ok) {
            throw new Error('鍒囨崲浠撳簱澶辫触');
        }
        
        const result = await response.json();
        
        // 鏇存柊褰撳墠浠撳簱
        currentWarehouse = userWarehouses.find(w => w.id === warehouseId);
        currentUser.current_warehouse_id = warehouseId;
        
        // 鏇存柊鏈湴瀛樺偍
        if (localStorage.getItem('access_token')) {
            localStorage.setItem('user', JSON.stringify(currentUser));
        }
        if (sessionStorage.getItem('access_token')) {
            sessionStorage.setItem('user', JSON.stringify(currentUser));
        }
        
        // 鏇存柊鏄剧ず
        updateWarehouseDisplay();
        updateWarehouseList();
        
        // 鍏抽棴涓嬫媺妗?
        document.getElementById('warehouseDropdown').classList.add('hidden');
        
        // 鏄剧ず鎻愮ず
        showToast('浠撳簱鍒囨崲鎴愬姛', 'success');
        
        // 鍒锋柊椤甸潰鏁版嵁
        if (typeof loadLocations === 'function') loadLocations();
        if (typeof loadGoods === 'function') loadGoods();
        if (typeof loadStock === 'function') loadStock();
        if (typeof loadInboundOrders === 'function') loadInboundOrders();
        if (typeof loadOutboundOrders === 'function') loadOutboundOrders();
        if (typeof loadCheckRecords === 'function') loadCheckRecords();
        
    } catch (error) {
        console.error('鍒囨崲浠撳簱澶辫触:', error);
        showToast('鍒囨崲浠撳簱澶辫触', 'error');
    }
}

// 璁剧疆鐢ㄦ埛鑿滃崟
function setupUserMenu() {
    const userMenu = document.getElementById('userMenu');
    const dropdown = document.getElementById('userDropdown');
    
    if (!userMenu || !dropdown) return;
    
    // 鐐瑰嚮鐢ㄦ埛鑿滃崟鍒囨崲涓嬫媺妗?
    userMenu.addEventListener('click', function(e) {
        e.stopPropagation();
        dropdown.classList.toggle('hidden');
    });
    
    // 鐐瑰嚮鍏朵粬鍦版柟鍏抽棴涓嬫媺妗?
    document.addEventListener('click', function() {
        dropdown.classList.add('hidden');
    });
    
    // 闃绘涓嬫媺妗嗗唴鐐瑰嚮浜嬩欢鍐掓场
    dropdown.addEventListener('click', function(e) {
        e.stopPropagation();
    });
}

// 璁剧疆閫€鍑虹櫥褰?
function setupLogout() {
    const logoutButton = document.getElementById('logoutButton');
    
    if (logoutButton) {
        logoutButton.addEventListener('click', function(e) {
            e.preventDefault();
            logout();
        });
    }
}

function isEmbeddedMode() {
    const params = new URLSearchParams(window.location.search);
    return window.top !== window.self || params.get('embedded') === '1';
}

function initPageAgent() {
    if (window.__WMS_PAGE_AGENT_LOADER__) return;

    const script = document.createElement('script');
    script.type = 'module';
    script.src = 'assets/js/page-agent-loader.js';
    script.setAttribute('data-page-agent-loader', '1');
    script.onerror = () => console.error('PageAgent loader 加载失败');

    window.__WMS_PAGE_AGENT_LOADER__ = true;
    document.body.appendChild(script);
}
// 閫€鍑虹櫥褰?
function logout() {
    // ??????
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    localStorage.removeItem('token_expiry');
    sessionStorage.removeItem('access_token');
    sessionStorage.removeItem('user');
    sessionStorage.removeItem('token_expiry');

    // ??????
    window.location.href = 'index.html';
}


// 鑾峰彇璇锋眰澶?
function getHeaders() {
    const token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token') || localStorage.getItem('token');
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    };
}


// 鏄剧ず鎻愮ず娑堟伅
function showToast(message, type = 'info') {
    // 鍒涘缓鎻愮ず鍏冪礌
    const toast = document.createElement('div');
    toast.className = `fixed top-4 right-4 px-6 py-3 rounded-md shadow-lg z-50 transform transition-all duration-300 translate-x-full opacity-0`;
    
    // 鏍规嵁绫诲瀷璁剧疆鏍峰紡
    if (type === 'success') {
        toast.classList.add('bg-green-500', 'text-white');
        toast.innerHTML = `<i class="fa fa-check-circle mr-2"></i>${message}`;
    } else if (type === 'error') {
        toast.classList.add('bg-red-500', 'text-white');
        toast.innerHTML = `<i class="fa fa-times-circle mr-2"></i>${message}`;
    } else if (type === 'warning') {
        toast.classList.add('bg-yellow-500', 'text-white');
        toast.innerHTML = `<i class="fa fa-exclamation-triangle mr-2"></i>${message}`;
    } else {
        toast.classList.add('bg-blue-500', 'text-white');
        toast.innerHTML = `<i class="fa fa-info-circle mr-2"></i>${message}`;
    }
    
    // 娣诲姞鍒伴〉闈?
    document.body.appendChild(toast);
    
    // 鏄剧ず鎻愮ず
    setTimeout(() => {
        toast.classList.remove('translate-x-full', 'opacity-0');
    }, 100);
    
    // 鑷姩闅愯棌
    setTimeout(() => {
        toast.classList.add('translate-x-full', 'opacity-0');
        
        // 绉婚櫎鍏冪礌
        setTimeout(() => {
            document.body.removeChild(toast);
        }, 300);
    }, 3000);
}

// 鑾峰彇褰撳墠鐢ㄦ埛瑙掕壊
function getCurrentUserRole() {
    return currentUser ? currentUser.role : 'operator';
}

// 鑾峰彇浠撳簱鍚嶇О
function getWarehouseName(warehouseId) {
    const warehouse = userWarehouses.find(w => w.id === warehouseId);
    return warehouse ? warehouse.name : '鏈煡浠撳簱';
}

// 鏍煎紡鍖栨棩鏈熸椂闂?
function formatDateTime(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

// 鏍煎紡鍖栨棩鏈?
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    });
}

// 鏍煎紡鍖栭噾棰?
function formatCurrency(amount) {
    return new Intl.NumberFormat('zh-CN', {
        style: 'currency',
        currency: 'CNY',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).format(amount);
}

// 鐢熸垚闅忔満ID
function generateId() {
    return Date.now().toString(36) + Math.random().toString(36).substr(2);
}

// 楠岃瘉琛ㄥ崟瀛楁
function validateField(value, type = 'required', min = null, max = null) {
    if (type === 'required' && !value) {
        return false;
    }
    
    if (type === 'number' && value) {
        const num = parseFloat(value);
        if (isNaN(num)) return false;
        if (min !== null && num < min) return false;
        if (max !== null && num > max) return false;
    }
    
    if (type === 'length' && value) {
        if (min !== null && value.length < min) return false;
        if (max !== null && value.length > max) return false;
    }
    
    return true;
}

// 闃叉姈鍑芥暟
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// 鑺傛祦鍑芥暟
function throttle(func, limit) {
    let inThrottle;
    return function() {
        const args = arguments;
        const context = this;
        if (!inThrottle) {
            func.apply(context, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}


