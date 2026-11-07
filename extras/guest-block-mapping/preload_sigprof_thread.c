#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>

// Linux per-thread CPU-time sampling via SIGPROF; no perf_event privilege.
// A 5ms sampling interval estimates fractional CPU time in the target thread.
typedef int (*main_fn)(int,char **,char **);
typedef int (*start_main_fn)(main_fn,int,char **,void(*)(void),void(*)(void),void(*)(void),void *);
typedef int (*create_fn)(pthread_t *,const pthread_attr_t *,void *(*)(void *),void *);
struct sample{uint64_t monotonic_ns;uint64_t rip;uint32_t tid;uint32_t reserved;};
static int sample_fd=-1;static main_fn real_main;
static int period_us=5000;
static volatile sig_atomic_t lost=0;
static __thread timer_t thread_timer;
static __thread int thread_timer_started=0;

static void on_sigprof(int signo,siginfo_t *si,void *u){
 (void)signo;(void)si;
 if(sample_fd<0)return;
 ucontext_t *ctx=(ucontext_t*)u;
 struct timespec ts;
 if(clock_gettime(CLOCK_MONOTONIC,&ts)!=0){lost++;return;}
 struct sample s={(uint64_t)ts.tv_sec*1000000000ULL+(uint64_t)ts.tv_nsec,
#if defined(__x86_64__)
 (uint64_t)ctx->uc_mcontext.gregs[REG_RIP],
#else
 0,
#endif
 (uint32_t)syscall(SYS_gettid),0};
 if(write(sample_fd,&s,sizeof(s))!=(ssize_t)sizeof(s))lost++;
}
static void start_timer(void){
 if(sample_fd<0||thread_timer_started)return;
 struct sigevent sev={0};
 sev.sigev_notify=SIGEV_THREAD_ID;
 sev.sigev_signo=SIGPROF;
 sev._sigev_un._tid=(int)syscall(SYS_gettid);
 if(timer_create(CLOCK_THREAD_CPUTIME_ID,&sev,&thread_timer)!=0){return;}
 struct itimerspec tm={0};
 tm.it_value.tv_sec=period_us/1000000;tm.it_value.tv_nsec=(period_us%1000000)*1000;
 tm.it_interval=tm.it_value;
 if(timer_settime(thread_timer,0,&tm,NULL)!=0){timer_delete(thread_timer);return;}
 thread_timer_started=1;
}
static void stop_timer(void){if(thread_timer_started){timer_delete(thread_timer);thread_timer_started=0;}}
struct trampoline_args{void *(*fn)(void *);void *arg;};
static void *trampoline(void *arg){
 struct trampoline_args a=*(struct trampoline_args*)arg;
 free(arg);
 start_timer();
 void *result=a.fn(a.arg);
 stop_timer();
 return result;
}
int pthread_create(pthread_t *thr,const pthread_attr_t *attr,void *(*fn)(void *),void *arg){
 static create_fn original=NULL;
 if(!original)original=(create_fn)dlsym(RTLD_NEXT,"pthread_create");
 if(!sample_fd||!fn)return original(thr,attr,fn,arg);
 struct trampoline_args *a=malloc(sizeof(*a));
 if(!a)return original(thr,attr,fn,arg);
 *a=(struct trampoline_args){.fn=fn,.arg=arg};
 int result=original(thr,attr,trampoline,a);
 if(result)free(a);
 return result;
}
static int wrapped_main(int argc,char **argv,char **envp){
 const char *path=getenv("DOSBOX_PROFILE_OUTPUT");
 const char *ps=getenv("DOSBOX_PROFILE_PERIOD_US");
 if(ps){int n=atoi(ps);if(n>=1000&&n<=1000000)period_us=n;}
 if(path&&*path){
   sample_fd=open(path,O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC|O_APPEND,0644);
   if(sample_fd<0)perror("profile open");
   else{
     struct sigaction sa={0};sa.sa_sigaction=on_sigprof;sa.sa_flags=SA_SIGINFO|SA_RESTART;
     sigemptyset(&sa.sa_mask);
     if(sigaction(SIGPROF,&sa,NULL)<0)perror("profile sigaction");
     start_timer();
     fprintf(stderr,"THREAD_SIGPROF_PROFILE_STARTED period_us=%d pid=%ld output=%s\n",period_us,(long)getpid(),path);
   }
 }
 int ret=real_main(argc,argv,envp);
 stop_timer();
 if(sample_fd>=0){close(sample_fd);sample_fd=-1;fprintf(stderr,"THREAD_SIGPROF_PROFILE_ENDED lost=%d\n",(int)lost);}
 return ret;
}
int __libc_start_main(main_fn main,int argc,char **ubp_av,void(*init)(void),void(*fini)(void),void(*rtld_fini)(void),void *stack_end){
 start_main_fn orig=(start_main_fn)dlsym(RTLD_NEXT,"__libc_start_main");
 real_main=main;
 return orig(wrapped_main,argc,ubp_av,init,fini,rtld_fini,stack_end);
}
