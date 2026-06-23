<?php
/**
 * 用户支付回调处理 —— 含多个明显代码问题，用于测试 AI Code Review
 */

// 问题1: 硬编码数据库密码
$db_host = "localhost";
$db_user = "root";
$db_pass = "admin123!@#";
$db_name = "payment_db";

$conn = new mysqli($db_host, $db_user, $db_pass, $db_name);

// 问题2: SQL注入 —— 直接拼接用户输入
$order_id = $_GET['order_id'];
$sql = "SELECT * FROM orders WHERE id = " . $order_id;
$result = $conn->query($sql);

// 问题3: XSS —— 直接输出用户输入
$username = $_GET['username'];
echo "<h1>欢迎, " . $username . "</h1>";

// 问题4: 敏感信息泄露到日志
$secret_key = "sk-live-9a8b7c6d5e4f3a2b1c0d";
error_log("处理支付回调, secret_key=" . $secret_key);

// 问题5: 弱密码哈希
$password = $_POST['password'];
$hash = md5($password);

// 问题6: 空catch吞异常
try {
    $amount = $_POST['amount'];
    if ($amount > 10000) {
        throw new Exception("金额异常");
    }
    processPayment($amount);
} catch (Exception $e) {
    // 啥也不做
}

// 问题7: 未验证的回调URL跳转
$redirect = $_GET['redirect'];
header("Location: " . $redirect);

function processPayment($amount) {
    // 问题8: 浮点数比较
    if ($amount == 100.00) {
        return true;
    }

    // 问题9: eval 动态代码执行
    $code = $_GET['callback'];
    eval($code);

    // 问题10: N+1 查询
    global $conn;
    $users = $conn->query("SELECT id FROM users");
    while ($row = $users->fetch_assoc()) {
        $uid = $row['id'];
        $orders = $conn->query("SELECT * FROM orders WHERE user_id = " . $uid);
    }

    return false;
}
