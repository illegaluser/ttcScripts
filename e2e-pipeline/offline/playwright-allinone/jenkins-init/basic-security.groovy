#!groovy
// Jenkins 부트스트랩 — All-in-One 오프라인 이미지 전용
// Dockerfile.allinone: COPY jenkins-init → /opt/jenkins-init, 이후 /opt/seed/jenkins-home/init.groovy.d/
// entrypoint-allinone.sh 가 seed 디렉토리를 $JENKINS_HOME 으로 복사 → Jenkins 기동 시 init.groovy.d/*.groovy 자동 실행
//
// 동작:
//   1. 로컬 계정 기반 SecurityRealm 생성 (admin / password)
//   2. 로그인한 사용자에게 전체 권한 부여 (익명은 차단)
//   3. setup wizard 완료 표시 (JENKINS_HOME/.lock.state 우회)
//
// 기본 자격증명을 바꾸려면 컨테이너 기동 시 다음 환경변수를 override 한다:
//   -e JENKINS_ADMIN_USER=... -e JENKINS_ADMIN_PW=...
// provision-apps.sh 가 동일 변수를 사용하므로 한 곳만 바꾸면 된다.
import jenkins.model.Jenkins
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import jenkins.install.InstallState

def adminUser = System.getenv('JENKINS_ADMIN_USER') ?: 'admin'
def adminPw   = System.getenv('JENKINS_ADMIN_PW')   ?: 'password'

def instance = Jenkins.get()

def realm = new HudsonPrivateSecurityRealm(false)
realm.createAccount(adminUser, adminPw)
instance.setSecurityRealm(realm)

def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
instance.setAuthorizationStrategy(strategy)

instance.setInstallState(InstallState.INITIAL_SETUP_COMPLETED)
instance.save()

println "[basic-security.groovy] admin user '${adminUser}' bootstrapped; setup wizard skipped."
